<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { getTranslationConfig, setTranslationConfig, getTranslationModels, getTranslationOpenAIModels } from '$lib/apis/configs';
	import { getOpenAIConfig, updateOpenAIConfig, getOpenAIModels } from '$lib/apis/openai';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import OpenAIConnection from './Connections/OpenAIConnection.svelte';
	import AddConnectionModal from '$lib/components/AddConnectionModal.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let config = null;
	let availableModels = [];
	let loadingModels = false;

	// OpenAI connections for translation provider
	let OPENAI_API_KEYS = [''];
	let OPENAI_API_BASE_URLS = [''];
	let OPENAI_API_CONFIGS = {};
	let ENABLE_OPENAI_API: null | boolean = null;
	let showAddOpenAIConnectionModal = false;
	let pipelineUrls = {};

	const submitHandler = async () => {
		const res = await setTranslationConfig(localStorage.token, config);
	};

	const fetchModels = async () => {
		loadingModels = true;
		try {
			let res;
			if (config.TRANSLATION_PROVIDER_ID) {
				// Fetch models from OpenAI provider
				res = await getTranslationOpenAIModels(localStorage.token, config.TRANSLATION_PROVIDER_ID);
			} else {
				// Fetch models from Google
				res = await getTranslationModels(localStorage.token);
			}

			if (res && res.models) {
				availableModels = res.models;
			}
		} catch (error) {
			console.error('Failed to fetch translation models:', error);
		} finally {
			loadingModels = false;
		}
	};

	const handleProviderChange = async () => {
		// Clear current models and fetch new ones
		availableModels = [];
		config.TRANSLATION_MODEL_ID = '';
		await fetchModels();
	};

	const updateOpenAIHandler = async () => {
		if (ENABLE_OPENAI_API !== null) {
			// Remove trailing slashes
			OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS.map((url) => url.replace(/\/$/, ''));

			// Check if API KEYS length is same than API URLS length
			if (OPENAI_API_KEYS.length !== OPENAI_API_BASE_URLS.length) {
				// if there are more keys than urls, remove the extra keys
				if (OPENAI_API_KEYS.length > OPENAI_API_BASE_URLS.length) {
					OPENAI_API_KEYS = OPENAI_API_KEYS.slice(0, OPENAI_API_BASE_URLS.length);
				}

				// if there are more urls than keys, add empty keys
				if (OPENAI_API_KEYS.length < OPENAI_API_BASE_URLS.length) {
					const diff = OPENAI_API_BASE_URLS.length - OPENAI_API_KEYS.length;
					for (let i = 0; i < diff; i++) {
						OPENAI_API_KEYS.push('');
					}
				}
			}

			const res = await updateOpenAIConfig(localStorage.token, {
				ENABLE_OPENAI_API: ENABLE_OPENAI_API,
				OPENAI_API_BASE_URLS: OPENAI_API_BASE_URLS,
				OPENAI_API_KEYS: OPENAI_API_KEYS,
				OPENAI_API_CONFIGS: OPENAI_API_CONFIGS
			}).catch((error) => {
				toast.error(`${error}`);
			});

			if (res) {
				toast.success($i18n.t('OpenAI API settings updated'));
			}
		}
	};

	const addOpenAIConnectionHandler = async (connection) => {
		OPENAI_API_BASE_URLS = [...OPENAI_API_BASE_URLS, connection.url];
		OPENAI_API_KEYS = [...OPENAI_API_KEYS, connection.key];
		OPENAI_API_CONFIGS[OPENAI_API_BASE_URLS.length - 1] = connection.config;

		await updateOpenAIHandler();
	};

	onMount(async () => {
		const res = await getTranslationConfig(localStorage.token);

		if (res) {
			config = res;
		}

		if (config && config.ENABLE_TRANSLATION) {
			await fetchModels();
		}

		// Load OpenAI config for provider selection
		const openaiConfig = await getOpenAIConfig(localStorage.token);
		if (openaiConfig) {
			ENABLE_OPENAI_API = openaiConfig.ENABLE_OPENAI_API;
			OPENAI_API_BASE_URLS = openaiConfig.OPENAI_API_BASE_URLS;
			OPENAI_API_KEYS = openaiConfig.OPENAI_API_KEYS;
			OPENAI_API_CONFIGS = openaiConfig.OPENAI_API_CONFIGS;

			if (ENABLE_OPENAI_API) {
				// get url and idx
				for (const [idx, url] of OPENAI_API_BASE_URLS.entries()) {
					if (!OPENAI_API_CONFIGS[idx]) {
						// Legacy support, url as key
						OPENAI_API_CONFIGS[idx] = OPENAI_API_CONFIGS[url] || {};
					}
				}

				OPENAI_API_BASE_URLS.forEach(async (url, idx) => {
					OPENAI_API_CONFIGS[idx] = OPENAI_API_CONFIGS[idx] || {};
					if (!(OPENAI_API_CONFIGS[idx]?.enable ?? true)) {
						return;
					}
					const res = await getOpenAIModels(localStorage.token, idx);
					if (res.pipelines) {
						pipelineUrls[url] = true;
					}
				});
			}
		}
	});
</script>

<AddConnectionModal
	bind:show={showAddOpenAIConnectionModal}
	onSubmit={addOpenAIConnectionHandler}
/>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await submitHandler();
		saveHandler();
	}}
>
	<div class="space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		{#if config}
			<div>
				<div class="mb-3.5">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Translation')}</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5">
						<div class="flex w-full justify-between">
							<div class="self-center text-xs font-medium">
								{$i18n.t('Enable Translation')}
							</div>

							<Switch bind:state={config.ENABLE_TRANSLATION} on:change={async () => {
								if (config.ENABLE_TRANSLATION) {
									await fetchModels();
								}
							}} />
						</div>
					</div>

					{#if config.ENABLE_TRANSLATION}
						<div class="mb-2.5 flex flex-col gap-1.5 w-full">
							<div class="text-xs font-medium">
								{$i18n.t('Translation Provider')}
							</div>

							<div class="flex w-full">
								<div class="flex-1">
									<select
										class="w-full text-sm py-0.5 bg-transparent outline-hidden"
										bind:value={config.TRANSLATION_PROVIDER_ID}
										on:change={handleProviderChange}
									>
										<option value="">{$i18n.t('Google (Default)')}</option>
										{#each OPENAI_API_BASE_URLS as url, idx}
											{#if OPENAI_API_CONFIGS[idx]?.enable ?? true}
												<option value={url}>{url}</option>
											{/if}
										{/each}
									</select>
								</div>
							</div>
						</div>

						<div class="mb-2.5 flex flex-col gap-1.5 w-full">
							<div class="text-xs font-medium">
								{$i18n.t('Translation Model')}
							</div>

							<div class="flex w-full">
								<div class="flex-1">
									{#if loadingModels}
										<div class="flex items-center justify-center py-2">
											<Spinner className="size-4" />
										</div>
									{:else if availableModels.length > 0}
										<select
											class="w-full text-sm py-0.5 bg-transparent outline-hidden"
											bind:value={config.TRANSLATION_MODEL_ID}
										>
											{#each availableModels as model}
												<option value={model.id}>{model.display_name}</option>
											{/each}
										</select>
									{:else}
										<input
											class="w-full text-sm py-0.5 placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-transparent outline-hidden"
											type="text"
											placeholder={$i18n.t('e.g. gemini-2.0-flash-exp')}
											bind:value={config.TRANSLATION_MODEL_ID}
											autocomplete="off"
										/>
									{/if}
								</div>
							</div>
						</div>

						<div class="mb-2.5">
							<div class="flex justify-between items-center">
								<div class="font-medium text-xs">{$i18n.t('Manage Translation Providers')}</div>

								<Tooltip content={$i18n.t(`Add Connection`)}>
									<button
										class="px-1"
										on:click={() => {
											showAddOpenAIConnectionModal = true;
										}}
										type="button"
									>
										<Plus />
									</button>
								</Tooltip>
							</div>

							<div class="flex flex-col gap-1.5 mt-1.5">
								{#each OPENAI_API_BASE_URLS as url, idx}
									<OpenAIConnection
										bind:url={OPENAI_API_BASE_URLS[idx]}
										bind:key={OPENAI_API_KEYS[idx]}
										bind:config={OPENAI_API_CONFIGS[idx]}
										pipeline={pipelineUrls[url] ? true : false}
										onSubmit={() => {
											updateOpenAIHandler();
										}}
										onDelete={() => {
											OPENAI_API_BASE_URLS = OPENAI_API_BASE_URLS.filter(
												(url, urlIdx) => idx !== urlIdx
											);
											OPENAI_API_KEYS = OPENAI_API_KEYS.filter((key, keyIdx) => idx !== keyIdx);

											let newConfig = {};
											OPENAI_API_BASE_URLS.forEach((url, newIdx) => {
												newConfig[newIdx] =
													OPENAI_API_CONFIGS[newIdx < idx ? newIdx : newIdx + 1];
											});
											OPENAI_API_CONFIGS = newConfig;
											updateOpenAIHandler();
										}}
									/>
								{/each}
							</div>
						</div>

						<div class="flex gap-2 w-full items-center justify-between">
							<div class="text-xs font-medium">
								{$i18n.t('Cache TTL (seconds)')}
							</div>

							<div class="">
								<Tooltip content={$i18n.t('Translation cache time-to-live in seconds')}>
									<input
										class="w-fit rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
										type="number"
										bind:value={config.TRANSLATION_CACHE_TTL}
										placeholder={$i18n.t('e.g. 3600')}
										autocomplete="off"
									/>
								</Tooltip>
							</div>
						</div>

						<div class="text-gray-500 text-xs mt-2">
							{#if config.TRANSLATION_PROVIDER_ID}
								{$i18n.t('Note: Using OpenAI-compatible provider for translation.')}
							{:else}
								{$i18n.t('Note: GOOGLE_API_KEY environment variable must be set for Google translation to work.')}
							{/if}
						</div>
					{/if}
				</div>
			</div>
		{/if}
	</div>
	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
