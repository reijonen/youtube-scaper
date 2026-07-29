<script lang="ts">
	import type { ChannelNode } from '$lib/server/queries/channelGraph';
	import type { SeedBreakdownEntry } from './breakdown';

	let {
		x,
		y,
		channel,
		breakdown
	}: { x: number; y: number; channel: ChannelNode; breakdown: SeedBreakdownEntry[] } = $props();
</script>

<div class="tooltip" style="left: {x + 12}px; top: {y + 12}px;">
	<strong>{channel.label}</strong>
	<table>
		<thead>
			<tr>
				<th>Seed</th>
				<th>Weight</th>
				<th>Credits</th>
			</tr>
		</thead>
		<tbody>
			{#each breakdown as entry (entry.seedId)}
				<tr class={entry.kind}>
					<td>{entry.seedId}</td>
					<td>{entry.weight.toFixed(3)}</td>
					<td>{entry.credits}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.tooltip {
		position: fixed;
		z-index: 100;
		background: rgba(20, 20, 20, 0.95);
		color: white;
		border: 1px solid rgba(255, 255, 255, 0.2);
		border-radius: 4px;
		padding: 8px 10px;
		font-size: 12px;
		pointer-events: none;
		max-width: 320px;
	}

	table {
		border-collapse: collapse;
		margin-top: 4px;
	}

	th,
	td {
		text-align: left;
		padding: 1px 8px 1px 0;
	}

	.collaborator {
		opacity: 0.6;
	}
</style>
