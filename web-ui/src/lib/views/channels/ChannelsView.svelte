<script lang="ts">
	import type { ChannelGraph } from '$lib/server/queries/channelGraph';
	import TotalModeGraph from './TotalModeGraph.svelte';

	let { data }: { data: ChannelGraph } = $props();

	const seedCount = $derived(data.nodes.filter((n) => n.kind === 'seed').length);
	const channelCount = $derived(data.nodes.filter((n) => n.kind === 'channel').length);
	const weightedEdgeCount = $derived(data.edges.filter((e) => e.kind === 'weighted').length);
	const collaboratorEdgeCount = $derived(
		data.edges.filter((e) => e.kind === 'collaborator').length
	);
</script>

<dl>
	<dt>Seed videos</dt>
	<dd>{seedCount}</dd>
	<dt>Channel nodes</dt>
	<dd>{channelCount}</dd>
	<dt>Weighted edges</dt>
	<dd>{weightedEdgeCount}</dd>
	<dt>Collaborator edges</dt>
	<dd>{collaboratorEdgeCount}</dd>
</dl>

<!-- Per-seed ranked mode and the mode toggle are Phase 6. -->
<TotalModeGraph {data} />
