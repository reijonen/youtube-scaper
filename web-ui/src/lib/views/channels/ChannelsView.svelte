<script lang="ts">
	import type { ChannelGraph } from '$lib/server/queries/channelGraph';

	let { data }: { data: ChannelGraph } = $props();

	const seedCount = $derived(data.nodes.filter((n) => n.kind === 'seed').length);
	const channelCount = $derived(data.nodes.filter((n) => n.kind === 'channel').length);
	const weightedEdgeCount = $derived(data.edges.filter((e) => e.kind === 'weighted').length);
	const collaboratorEdgeCount = $derived(
		data.edges.filter((e) => e.kind === 'collaborator').length
	);
</script>

<!-- Placeholder pending Phase 5/6 (Sigma total mode, ranked per-seed mode). -->
<dl>
	<dt>Seed videos</dt>
	<dd>{seedCount}</dd>
	<dt>Channel nodes</dt>
	<dd>{channelCount}</dd>
	<dt>Weighted edges</dt>
	<dd>{weightedEdgeCount}</dd>
	<dt>Dashed collaborator edges</dt>
	<dd>{collaboratorEdgeCount}</dd>
</dl>
