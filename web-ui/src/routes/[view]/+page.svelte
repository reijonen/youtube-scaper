<script lang="ts">
	import { getView } from '$lib/views';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const view = $derived(getView(data.viewId)!);
</script>

<h1>{view.title}</h1>
<p>{view.question}</p>

<view.component data={data.viewData} />
