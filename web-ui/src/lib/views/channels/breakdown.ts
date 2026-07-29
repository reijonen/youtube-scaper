import type { ChannelGraph } from '$lib/server/queries/channelGraph';

export interface SeedBreakdownEntry {
	seedId: string;
	weight: number;
	credits: number;
	kind: 'weighted' | 'collaborator';
}

/** A channel's per-seed weighted score and credit count, for every seed it appears in. */
export function channelBreakdown(data: ChannelGraph, channelId: string): SeedBreakdownEntry[] {
	return data.edges
		.filter((edge) => edge.target === channelId)
		.map((edge) =>
			edge.kind === 'weighted'
				? { seedId: edge.source, weight: edge.weight, credits: edge.credits, kind: edge.kind }
				: { seedId: edge.source, weight: 0, credits: edge.credits, kind: edge.kind }
		)
		.sort((a, b) => b.weight - a.weight);
}
