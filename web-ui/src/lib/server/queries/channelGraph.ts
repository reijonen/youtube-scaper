import type { Database } from 'better-sqlite3';

export interface SeedNode {
	kind: 'seed';
	id: string;
	hasRecommendations: boolean;
}

export interface ChannelNode {
	kind: 'channel';
	id: string;
	label: string;
	totalWeight: number;
	totalCredits: number;
}

export type GraphNode = SeedNode | ChannelNode;

export interface WeightedEdge {
	kind: 'weighted';
	source: string;
	target: string;
	weight: number;
	credits: number;
}

export interface CollaboratorEdge {
	kind: 'collaborator';
	source: string;
	target: string;
	credits: number;
}

export type GraphEdge = WeightedEdge | CollaboratorEdge;

export interface ChannelGraph {
	nodes: GraphNode[];
	edges: GraphEdge[];
}

/**
 * Maps a seed video to its graph node. Today this is the identity - a seed's node id is
 * its own video_id. A future channel-to-channel view replaces exactly this function with
 * a video-to-channel mapping held outside this database; nothing else changes.
 */
export function resolveSeedSource(videoId: string): string {
	return videoId;
}

function dcgWeight(rawPosition: number): number {
	if (rawPosition < 1) {
		throw new Error(`raw_position must be >= 1, got ${rawPosition}`);
	}
	return 1 / Math.log2(1 + rawPosition);
}

interface SeedRow {
	video_id: string;
}

interface RecommendationChannelRow {
	seed_id: string;
	recommendation_id: number;
	raw_position: number;
	channel_position: number;
	channel_id: string;
	name: string | null;
	handle: string | null;
}

interface PairAccumulator {
	seedId: string;
	channelId: string;
	weightedSum: number;
	weightedCredits: number;
	collaboratorCredits: number;
}

const PAIR_KEY_SEPARATOR = '::';

export function getChannelGraph(db: Database): ChannelGraph {
	const seedRows = db
		.prepare<[], SeedRow>(`SELECT video_id FROM videos WHERE status = 'completed'`)
		.all();

	const seedIds = seedRows.map((row) => row.video_id);
	const seedsWithRecommendations = new Set<string>();

	const channelRows =
		seedIds.length === 0
			? []
			: db
					.prepare<
						unknown[],
						RecommendationChannelRow
					>(
						`SELECT
							r.video_id AS seed_id,
							r.id AS recommendation_id,
							r.raw_position AS raw_position,
							rc.position AS channel_position,
							rc.channel_id AS channel_id,
							rc.name AS name,
							rc.handle AS handle
						FROM recommendations r
						JOIN recommendation_channels rc ON rc.recommendation_id = r.id
						WHERE r.video_id IN (${seedIds.map(() => '?').join(',')})`
					)
					.all(...seedIds);

	// (seed_id, channel_id) -> accumulator
	const pairs = new Map<string, PairAccumulator>();
	const channelLabels = new Map<string, string>();

	for (const row of channelRows) {
		seedsWithRecommendations.add(row.seed_id);

		const label = row.handle ?? row.name ?? row.channel_id;
		if (!channelLabels.has(row.channel_id)) {
			channelLabels.set(row.channel_id, label);
		}

		const pairKey = row.seed_id + PAIR_KEY_SEPARATOR + row.channel_id;
		let pair = pairs.get(pairKey);
		if (!pair) {
			pair = {
				seedId: row.seed_id,
				channelId: row.channel_id,
				weightedSum: 0,
				weightedCredits: 0,
				collaboratorCredits: 0
			};
			pairs.set(pairKey, pair);
		}

		if (row.channel_position === 0) {
			pair.weightedSum += dcgWeight(row.raw_position);
			pair.weightedCredits += 1;
		} else {
			pair.collaboratorCredits += 1;
		}
	}

	const seedNodes: SeedNode[] = seedRows.map((row) => ({
		kind: 'seed',
		id: resolveSeedSource(row.video_id),
		hasRecommendations: seedsWithRecommendations.has(row.video_id)
	}));

	const channelTotals = new Map<string, { totalWeight: number; totalCredits: number }>();
	for (const channelId of channelLabels.keys()) {
		channelTotals.set(channelId, { totalWeight: 0, totalCredits: 0 });
	}

	const edges: GraphEdge[] = [];

	for (const pair of pairs.values()) {
		const totals = channelTotals.get(pair.channelId);
		if (totals) {
			totals.totalWeight += pair.weightedSum;
			totals.totalCredits += pair.weightedCredits;
		}

		// A pair with any position-0 credit is a weighted edge; its collaborator
		// credits (if any) contribute nothing and produce no separate dashed edge.
		if (pair.weightedCredits > 0) {
			edges.push({
				kind: 'weighted',
				source: pair.seedId,
				target: pair.channelId,
				weight: pair.weightedSum,
				credits: pair.weightedCredits
			});
		} else if (pair.collaboratorCredits > 0) {
			edges.push({
				kind: 'collaborator',
				source: pair.seedId,
				target: pair.channelId,
				credits: pair.collaboratorCredits
			});
		}
	}

	const channelNodes: ChannelNode[] = Array.from(channelLabels.entries()).map(
		([channelId, label]) => {
			const totals = channelTotals.get(channelId) ?? { totalWeight: 0, totalCredits: 0 };
			return {
				kind: 'channel',
				id: channelId,
				label,
				totalWeight: totals.totalWeight,
				totalCredits: totals.totalCredits
			};
		}
	);

	return {
		nodes: [...seedNodes, ...channelNodes],
		edges
	};
}
