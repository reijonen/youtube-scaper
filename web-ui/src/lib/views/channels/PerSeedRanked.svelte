<script lang="ts">
	import type { ChannelGraph } from '$lib/server/queries/channelGraph';

	let {
		data,
		seedId,
		onHover
	}: {
		data: ChannelGraph;
		seedId: string;
		onHover?: (channelId: string | undefined, x?: number, y?: number) => void;
	} = $props();

	interface Row {
		channelId: string;
		label: string;
		weight: number;
		credits: number;
		kind: 'weighted' | 'collaborator';
	}

	const rows = $derived.by((): Row[] => {
		const labelById = new Map(
			data.nodes.filter((n) => n.kind === 'channel').map((n) => [n.id, n.label])
		);
		return data.edges
			.filter((edge) => edge.source === seedId)
			.map((edge): Row => ({
				channelId: edge.target,
				label: labelById.get(edge.target) ?? edge.target,
				weight: edge.kind === 'weighted' ? edge.weight : 0,
				credits: edge.credits,
				kind: edge.kind
			}))
			.sort((a, b) => b.weight - a.weight);
	});

	const maxWeight = $derived(rows.reduce((max, row) => Math.max(max, row.weight), 0));

	function barWidth(weight: number): number {
		if (maxWeight === 0) return 0;
		return Math.max((weight / maxWeight) * 100, weight > 0 ? 2 : 0);
	}
</script>

<ol class="ranked-list">
	{#each rows as row (row.channelId)}
		<li
			class={row.kind}
			onmouseenter={(e) => onHover?.(row.channelId, e.clientX, e.clientY)}
			onmouseleave={() => onHover?.(undefined)}
		>
			<img class="avatar" src="/avatars/{row.channelId}" alt="" width="24" height="24" />
			<span class="label">{row.label}</span>
			<span class="bar-track">
				<span class="bar" style="width: {barWidth(row.weight)}%"></span>
			</span>
			<span class="weight">{row.weight.toFixed(2)}</span>
			<span class="credits">×{row.credits}</span>
		</li>
	{/each}
</ol>

<style>
	.ranked-list {
		list-style: none;
		margin: 0;
		padding: 0;
		max-width: 640px;
	}

	li {
		display: grid;
		grid-template-columns: 24px 180px 1fr 48px 32px;
		align-items: center;
		gap: 8px;
		padding: 2px 0;
	}

	.avatar {
		border-radius: 50%;
		width: 24px;
		height: 24px;
	}

	.label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.bar-track {
		background: rgba(128, 128, 128, 0.2);
		height: 8px;
		display: block;
	}

	.bar {
		display: block;
		height: 100%;
		background: rgba(100, 100, 220, 0.8);
	}

	.collaborator .bar {
		background: rgba(150, 150, 150, 0.4);
	}

	.collaborator .label {
		opacity: 0.7;
	}

	.weight,
	.credits {
		font-variant-numeric: tabular-nums;
		text-align: right;
	}
</style>
