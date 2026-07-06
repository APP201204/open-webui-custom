import asyncio
import json
import logging
import random
import sys
import time
import uuid
from typing import Any, Optional

from aiocache import cached
from fastapi import HTTPException, Request, status
from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL, ENABLE_TRANSLATION, GLOBAL_LOG_LEVEL
from open_webui.functions import generate_function_chat_completion
from open_webui.models.functions import Functions
from open_webui.models.models import Models
from open_webui.models.users import UserModel
from open_webui.routers.ollama import (
    generate_chat_completion as generate_ollama_chat_completion,
)
from open_webui.routers.openai import (
    generate_chat_completion as generate_openai_chat_completion,
)
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
)
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
    sio,
)
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.models import check_model_access, get_all_models
from open_webui.utils.payload import convert_payload_openai_to_ollama
from open_webui.utils.response import (
    convert_response_ollama_to_openai,
    convert_streaming_response_ollama_to_openai,
)
from open_webui.utils.translation import (
    detect_language,
    restore_code_blocks,
    strip_code_blocks,
    translate_text,
)
from starlette.responses import JSONResponse, Response, StreamingResponse

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


async def _translate_response_content(content: str, user_language: str) -> str:
    """Translate response content from English to user's language.

    Strips code blocks, translates prose, then restores code blocks.
    """
    if not user_language or user_language == 'en':
        return content

    # Strip code blocks
    code_blocks, prose = strip_code_blocks(content)

    # Translate prose
    translated_prose = await translate_text(prose, 'en', user_language)

    # Restore code blocks
    return restore_code_blocks(translated_prose, code_blocks)


async def _stream_translated_response(stream, user_language: str):
    """Buffer full streaming response, translate, then emit as chunks."""
    if not user_language or user_language == 'en':
        # No translation needed, just pass through
        async for chunk in stream:
            yield chunk
        return

    # Buffer the full response
    full_content = ""
    async for chunk in stream:
        # Decode chunk if it's bytes
        if isinstance(chunk, bytes):
            chunk_str = chunk.decode('utf-8')
        else:
            chunk_str = chunk

        # Parse SSE chunks to extract content
        if chunk_str.startswith('data: '):
            data_str = chunk_str[6:].strip()
            if data_str == '[DONE]':
                continue
            try:
                data = json.loads(data_str)
                if 'choices' in data and len(data['choices']) > 0:
                    delta = data['choices'][0].get('delta', {})
                    if 'content' in delta:
                        full_content += delta['content']
            except json.JSONDecodeError:
                pass
        # Don't yield original chunks when translating - only show translated content

    # After buffering, translate the full content
    if full_content:
        translated = await _translate_response_content(full_content, user_language)

        # Emit translated content as simulated chunks
        # Split on sentence boundaries or reasonable chunk sizes
        chunk_size = 50  # characters per chunk
        for i in range(0, len(translated), chunk_size):
            chunk_text = translated[i:i + chunk_size]
            chunk_data = {
                'choices': [{'delta': {'content': chunk_text}, 'index': 0}],
                'model': 'translated',
            }
            yield f'data: {json.dumps(chunk_data)}\n\n'
            await asyncio.sleep(0.01)  # Small delay for streaming effect

        yield 'data: [DONE]\n\n'


# When the question has been asked, let silence not be the
# answer. But if the answer must wait, let it come honest.
async def generate_direct_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    models: dict,
):
    log.info('generate_direct_chat_completion')

    metadata = form_data.pop('metadata', {})

    user_id = metadata.get('user_id')
    session_id = metadata.get('session_id')
    request_id = str(uuid.uuid4())  # Generate a unique request ID

    event_caller = await get_event_call(metadata)
    if event_caller is None:
        raise Exception(
            'Direct connection requires an active WebSocket session; '
            'cannot generate completion in this context (e.g. background task).'
        )

    channel = f'{user_id}:{session_id}:{request_id}'
    logging.info(f'WebSocket channel: {channel}')

    if form_data.get('stream'):
        q = asyncio.Queue()

        async def message_listener(sid, data):
            """
            Handle received socket messages and push them into the queue.
            """
            await q.put(data)

        # Register the listener
        sio.on(channel, message_listener)

        # Start processing chat completion in background
        res = await event_caller(
            {
                'type': 'request:chat:completion',
                'data': {
                    'form_data': form_data,
                    'model': models[form_data['model']],
                    'channel': channel,
                    'session_id': session_id,
                },
            }
        )

        log.info(f'res: {res}')

        if res.get('status', False):
            # Define a generator to stream responses
            async def event_generator():
                nonlocal q
                try:
                    while True:
                        data = await q.get()  # Wait for new messages
                        if isinstance(data, dict):
                            if 'done' in data and data['done']:
                                break  # Stop streaming when 'done' is received

                            yield f'data: {json.dumps(data)}\n\n'
                        elif isinstance(data, str):
                            if 'data:' in data:
                                yield f'{data}\n\n'
                            else:
                                yield f'data: {data}\n\n'
                except Exception as e:
                    log.debug(f'Error in event generator: {e}')
                    pass

            # Define a background task to run the event generator
            async def background():
                try:
                    del sio.handlers['/'][channel]
                except Exception as e:
                    pass

            # Return the streaming response
            return StreamingResponse(event_generator(), media_type='text/event-stream', background=background)
        else:
            raise Exception(str(res))
    else:
        res = await event_caller(
            {
                'type': 'request:chat:completion',
                'data': {
                    'form_data': form_data,
                    'model': models[form_data['model']],
                    'channel': channel,
                    'session_id': session_id,
                },
            }
        )

        if 'error' in res and res['error']:
            raise Exception(res['error'])

        return res


async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user: Any,
    bypass_filter: bool = False,
    bypass_system_prompt: bool = False,
):
    log.debug(f'generate_chat_completion: {form_data}')
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    # Propagate bypass_filter and bypass_system_prompt via request.state so that
    # downstream route handlers (openai/ollama) can read them without exposing
    # them as query parameters.
    request.state.bypass_filter = bypass_filter
    request.state.bypass_system_prompt = bypass_system_prompt

    if hasattr(request.state, 'metadata'):
        if 'metadata' not in form_data:
            form_data['metadata'] = request.state.metadata
        else:
            form_data['metadata'] = {
                **form_data['metadata'],
                **request.state.metadata,
            }

    # Translation: Detect and translate user message to English before model call
    user_language = None
    original_user_message = None

    # Check if user_language is already in metadata (from previous call)
    if 'metadata' in form_data and 'user_language' in form_data['metadata']:
        user_language = form_data['metadata']['user_language']
        original_user_message = form_data['metadata'].get('original_user_message')
        log.info(f'Using existing user_language from metadata: {user_language}')

    if ENABLE_TRANSLATION and 'messages' in form_data and len(form_data['messages']) > 0:
        last_message = form_data['messages'][-1]
        if last_message.get('role') == 'user' and 'content' in last_message:
            content = last_message['content']
            if isinstance(content, str) and content.strip():
                # Only detect language if not already set
                if not user_language:
                    detected_lang = await detect_language(content)
                    log.info(f'Detected language: {detected_lang}')
                    if detected_lang != 'en':
                        user_language = detected_lang
                        original_user_message = content
                        translated_content = await translate_text(content, detected_lang, 'en')
                        form_data['messages'][-1]['content'] = translated_content
                        log.info(f'Translated user message from {detected_lang} to English')
                        # Store translation metadata
                        if 'metadata' not in form_data:
                            form_data['metadata'] = {}
                        form_data['metadata']['user_language'] = user_language
                        form_data['metadata']['original_user_message'] = original_user_message
                        log.info(f'Set user_language in metadata: {user_language}')

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        # Merge the direct connection model into server models so that
        # task functions (title, tags, etc.) can resolve a server-side
        # task model while still having the direct model available.
        models = {
            **request.app.state.MODELS,
            request.state.model['id']: request.state.model,
        }
        log.debug(f'direct connection to model: {request.state.model["id"]}')
    else:
        models = request.app.state.MODELS

    model_id = form_data['model']
    if model_id not in models:
        raise Exception('Model not found')

    model = models[model_id]

    if getattr(request.state, 'direct', False) and model_id == getattr(request.state, 'model', {}).get('id'):
        return await generate_direct_chat_completion(request, form_data, user=user, models=models)
    else:
        # Check if user has access to the model
        if not bypass_filter and user.role == 'user':
            try:
                await check_model_access(user, model)
            except Exception as e:
                raise e

        # Arena model — sub-model was already resolved by process_chat_payload.
        # Inject selected_model_id into the response for the frontend.
        metadata = form_data.get('metadata', {})
        selected_model_id = metadata.pop('selected_model_id', None)
        # Also clear from request.state.metadata to prevent the merge at
        # lines 177-179 from re-adding it on the recursive call.
        if hasattr(request.state, 'metadata'):
            request.state.metadata.pop('selected_model_id', None)

        # Fallback: if generate_chat_completion is called with an arena model
        # from a path that did NOT go through process_chat_payload (e.g.,
        # background tasks for title/follow-up/tags generation), resolve now.
        if not selected_model_id and model.get('owned_by') == 'arena':
            model_ids = model.get('info', {}).get('meta', {}).get('model_ids')
            filter_mode = model.get('info', {}).get('meta', {}).get('filter_mode')
            if model_ids and filter_mode == 'exclude':
                model_ids = [
                    available_model['id']
                    for available_model in list(request.app.state.MODELS.values())
                    if available_model.get('owned_by') != 'arena' and available_model['id'] not in model_ids
                ]

            if isinstance(model_ids, list) and model_ids:
                selected_model_id = random.choice(model_ids)
            else:
                model_ids = [
                    available_model['id']
                    for available_model in list(request.app.state.MODELS.values())
                    if available_model.get('owned_by') != 'arena'
                ]
                selected_model_id = random.choice(model_ids)

            form_data['model'] = selected_model_id

            # bypass_filter recursion below skips the line-200 check; gate the resolved model here.
            if not bypass_filter and user.role == 'user':
                selected_model = request.app.state.MODELS.get(selected_model_id)
                if selected_model:
                    await check_model_access(user, selected_model)

        if selected_model_id:
            if form_data.get('stream') == True:

                async def stream_wrapper(stream):
                    yield f'data: {json.dumps({"selected_model_id": selected_model_id})}\n\n'
                    async for chunk in stream:
                        yield chunk

                response = await generate_chat_completion(
                    request,
                    form_data,
                    user,
                    bypass_filter=True,
                    bypass_system_prompt=bypass_system_prompt,
                )
                return StreamingResponse(
                    stream_wrapper(response.body_iterator),
                    media_type='text/event-stream',
                    background=response.background,
                )
            else:
                return {
                    **(
                        await generate_chat_completion(
                            request,
                            form_data,
                            user,
                            bypass_filter=True,
                            bypass_system_prompt=bypass_system_prompt,
                        )
                    ),
                    'selected_model_id': selected_model_id,
                }

        if model.get('pipe'):
            # Below does not require bypass_filter because this is the only route the uses this function and it is already bypassing the filter
            response = await generate_function_chat_completion(request, form_data, user=user, models=models)
            if form_data.get('stream'):
                # Wrap streaming response with translation
                if ENABLE_TRANSLATION and user_language and user_language != 'en':
                    return StreamingResponse(
                        _stream_translated_response(response.body_iterator, user_language),
                        headers=dict(response.headers),
                        background=response.background,
                    )
                else:
                    return response
            else:
                # Post-translation for non-streaming responses
                log.info(f'Pipeline non-streaming: ENABLE_TRANSLATION={ENABLE_TRANSLATION}, user_language={user_language}')
                if ENABLE_TRANSLATION and user_language and user_language != 'en':
                    if isinstance(response, dict) and 'choices' in response and len(response['choices']) > 0:
                        content = response['choices'][0].get('message', {}).get('content', '')
                        log.info(f'Content to translate: {content[:100] if content else "empty"}...')
                        if content:
                            # Translate
                            translated = await _translate_response_content(content, user_language)
                            response['choices'][0]['message']['content'] = translated
                            log.info(f'Translated response from English to {user_language}')
                # Restore original user message if it was translated
                if original_user_message and 'messages' in form_data and len(form_data['messages']) > 0:
                    form_data['messages'][-1]['content'] = original_user_message
                return response

        if model.get('owned_by') == 'ollama':
            # Using /ollama/api/chat endpoint
            form_data = convert_payload_openai_to_ollama(form_data)
            response = await generate_ollama_chat_completion(
                request=request,
                form_data=form_data,
                user=user,
            )
            if form_data.get('stream'):
                response.headers['content-type'] = 'text/event-stream'
                # Wrap streaming response with translation
                if ENABLE_TRANSLATION and user_language and user_language != 'en':
                    converted_stream = convert_streaming_response_ollama_to_openai(response)
                    return StreamingResponse(
                        _stream_translated_response(converted_stream, user_language),
                        headers=dict(response.headers),
                        background=response.background,
                    )
                else:
                    return StreamingResponse(
                        convert_streaming_response_ollama_to_openai(response),
                        headers=dict(response.headers),
                        background=response.background,
                    )
            else:
                response = convert_response_ollama_to_openai(response)
                # Post-translation for non-streaming responses
                log.info(f'Ollama non-streaming: ENABLE_TRANSLATION={ENABLE_TRANSLATION}, user_language={user_language}')
                if ENABLE_TRANSLATION and user_language and user_language != 'en':
                    if isinstance(response, dict) and 'choices' in response and len(response['choices']) > 0:
                        content = response['choices'][0].get('message', {}).get('content', '')
                        log.info(f'Content to translate: {content[:100] if content else "empty"}...')
                        if content:
                            # Translate
                            translated = await _translate_response_content(content, user_language)
                            response['choices'][0]['message']['content'] = translated
                            log.info(f'Translated response from English to {user_language}')
                # Restore original user message if it was translated
                if original_user_message and 'messages' in form_data and len(form_data['messages']) > 0:
                    form_data['messages'][-1]['content'] = original_user_message
                return response
        else:
            response = await generate_openai_chat_completion(
                request=request,
                form_data=form_data,
                user=user,
            )
            if form_data.get('stream'):
                # Wrap streaming response with translation
                if ENABLE_TRANSLATION and user_language and user_language != 'en':
                    return StreamingResponse(
                        _stream_translated_response(response.body_iterator, user_language),
                        headers=dict(response.headers),
                        background=response.background,
                    )
                else:
                    return response
            else:
                # Post-translation for non-streaming responses
                log.info(f'OpenAI non-streaming: ENABLE_TRANSLATION={ENABLE_TRANSLATION}, user_language={user_language}')
                if ENABLE_TRANSLATION and user_language and user_language != 'en':
                    if isinstance(response, dict) and 'choices' in response and len(response['choices']) > 0:
                        content = response['choices'][0].get('message', {}).get('content', '')
                        log.info(f'Content to translate: {content[:100] if content else "empty"}...')
                        if content:
                            # Translate
                            translated = await _translate_response_content(content, user_language)
                            response['choices'][0]['message']['content'] = translated
                            log.info(f'Translated response from English to {user_language}')
                # Restore original user message if it was translated
                if original_user_message and 'messages' in form_data and len(form_data['messages']) > 0:
                    form_data['messages'][-1]['content'] = original_user_message
                return response


chat_completion = generate_chat_completion


async def chat_completed(request: Request, form_data: dict, user: Any):
    if not request.app.state.MODELS:
        await get_all_models(request, user=user)

    if getattr(request.state, 'direct', False) and hasattr(request.state, 'model'):
        models = {
            **request.app.state.MODELS,
            request.state.model['id']: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    data = form_data

    if not data.get('id'):
        raise Exception('Missing message id')

    model_id = data['model']
    if model_id not in models:
        raise Exception('Model not found')

    model = models[model_id]

    try:
        data = await process_pipeline_outlet_filter(request, data, user, models)
    except HTTPException:
        raise
    except Exception as e:
        raise Exception(f'Error: {e}')

    if not data.get('id'):
        raise Exception('Missing message id')

    metadata = {
        'chat_id': data['chat_id'],
        'message_id': data['id'],
        'filter_ids': data.get('filter_ids', []),
        'session_id': data['session_id'],
        'user_id': user.id,
    }

    extra_params = {
        '__event_emitter__': await get_event_emitter(metadata),
        '__event_call__': await get_event_call(metadata),
        '__user__': user.model_dump() if isinstance(user, UserModel) else {},
        '__metadata__': metadata,
        '__request__': request,
        '__model__': model,
    }

    try:
        filter_ids = await get_sorted_filter_ids(request, model, metadata.get('filter_ids', []))
        filter_functions = await Functions.get_functions_by_ids(filter_ids)

        result, _ = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type='outlet',
            form_data=data,
            extra_params=extra_params,
        )
        return result
    except Exception as e:
        raise Exception(f'Error: {e}')
