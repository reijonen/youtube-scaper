<script lang="ts">
	import type { ChannelGraph, ChannelNode } from '$lib/server/queries/channelGraph';
	import TotalModeGraph from './TotalModeGraph.svelte';
	import PerSeedRanked from './PerSeedRanked.svelte';
	import HoverTooltip from './HoverTooltip.svelte';
	import { channelBreakdown } from './breakdown';

	let { data }: { data: ChannelGraph } = $props();

	const seedCount = $derived(data.nodes.filter((n) => n.kind === 'seed').length);
	const channelCount = $derived(data.nodes.filter((n) => n.kind === 'channel').length);
	const weightedEdgeCount = $derived(data.edges.filter((e) => e.kind === 'weighted').length);
	const collaboratorEdgeCount = $derived(
		data.edges.filter((e) => e.kind === 'collaborator').length
	);

	const seedIds = $derived(data.nodes.filter((n) => n.kind === 'seed').map((n) => n.id));

	type Mode = 'total' | 'per-seed';
	let mode: Mode = $state('total');
	// Lives here, not inside PerSeedRanked, so it survives a toggle back and forth
	// between modes instead of resetting every time the mode switches.
	let selectedSeedId: string | undefined = $state(undefined);

	$effect(() => {
		if (selectedSeedId === undefined && seedIds.length > 0) {
			selectedSeedId = seedIds[0];
		}
	});

	let hover: { channelId: string; x: number; y: number } | undefined = $state(undefined);

	function handleHover(channelId: string | undefined, x?: number, y?: number): void {
		hover = channelId ? { channelId, x: x ?? 0, y: y ?? 0 } : undefined;
	}

	const hoveredChannel = $derived.by((): ChannelNode | undefined => {
		const current = hover;
		if (!current) return undefined;
		return data.nodes.find((n): n is ChannelNode => n.kind === 'channel' && n.id === current.channelId);
	});

	const hoveredBreakdown = $derived.by(() => {
		const current = hover;
		return current ? channelBreakdown(data, current.channelId) : [];
	});
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

<div class="controls">
	<button class:active={mode === 'total'} onclick={() => (mode = 'total')}>Total</button>
	<button class:active={mode === 'per-seed'} onclick={() => (mode = 'per-seed')}>Per-seed</button>
	{#if mode === 'per-seed'}
		<select bind:value={selectedSeedId}>
			{#each seedIds as id (id)}
				<option value={id}>{id}</option>
			{/each}
		</select>
	{/if}
</div>

{#if mode === 'total'}
	<TotalModeGraph {data} onHover={handleHover} />
{:else if selectedSeedId}
	<PerSeedRanked {data} seedId={selectedSeedId} onHover={handleHover} />
{/if}

{#if hover && hoveredChannel}
	<HoverTooltip x={hover.x} y={hover.y} channel={hoveredChannel} breakdown={hoveredBreakdown} />
{/if}

<style>
	.controls {
		display: flex;
		gap: 8px;
		align-items: center;
		margin: 12px 0;
	}

	button.active {
		font-weight: bold;
		text-decoration: underline;
	}
</style>
