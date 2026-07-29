<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';
	import { views } from '$lib/views';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<nav>
	<a href="/">web-ui</a>
	{#each views as view (view.id)}
		<a href="/{view.id}">{view.title}</a>
	{/each}
</nav>

{@render children()}
