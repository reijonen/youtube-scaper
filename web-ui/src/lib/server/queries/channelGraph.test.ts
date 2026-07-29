import { describe, expect, it } from 'vitest';
import { createFixtureDb, type Fixture } from './__fixtures__/testDb';
import { getChannelGraph, type ChannelNode, type WeightedEdge } from './channelGraph';

const fixture: Fixture = {
	videos: [
		{ video_id: 'seed1', status: 'completed' },
		{ video_id: 'seed2', status: 'completed' },
		{ video_id: 'seed3', status: 'completed' }, // no recommendations - isolated node
		{ video_id: 'failed1', status: 'failed' } // must never appear as a seed
	],
	recommendations: [
		// chA credited twice in seed1 (raw_position 1 and 3) - must merge into one edge.
		{
			video_id: 'seed1',
			recommended_video_id: 'r1',
			raw_position: 1,
			channels: [{ position: 0, channel_id: 'chA', handle: '@chA' }]
		},
		{
			video_id: 'seed1',
			recommended_video_id: 'r2',
			raw_position: 3,
			channels: [{ position: 0, channel_id: 'chA', handle: '@chA' }]
		},
		// chB is the main channel; chC is a collaborator that never appears at position 0
		// anywhere - it must still be a node, with zero weight.
		{
			video_id: 'seed1',
			recommended_video_id: 'r3',
			raw_position: 7,
			channels: [
				{ position: 0, channel_id: 'chB', handle: '@chB' },
				{ position: 1, channel_id: 'chC', handle: '@chC' }
			]
		},
		// chF is the main channel here (weighted)...
		{
			video_id: 'seed1',
			recommended_video_id: 'r4',
			raw_position: 2,
			channels: [{ position: 0, channel_id: 'chF', handle: '@chF' }]
		},
		// ...and a collaborator on a different recommendation in the SAME seed. The
		// (seed1, chF) pair must resolve to a single weighted edge with no dashed edge.
		{
			video_id: 'seed1',
			recommended_video_id: 'r5',
			raw_position: 9,
			channels: [
				{ position: 0, channel_id: 'chG', handle: '@chG' },
				{ position: 1, channel_id: 'chF', handle: '@chF' }
			]
		},
		// chA also appears in seed2 - must be a distinct edge from seed1's chA edge.
		{
			video_id: 'seed2',
			recommended_video_id: 'r6',
			raw_position: 1,
			channels: [{ position: 0, channel_id: 'chA', handle: '@chA' }]
		}
	]
};

function buildGraph() {
	const db = createFixtureDb(fixture);
	try {
		return getChannelGraph(db);
	} finally {
		db.close();
	}
}

function findChannel(nodes: ChannelNode[], id: string): ChannelNode {
	const node = nodes.find((n) => n.id === id);
	if (!node) throw new Error(`channel node ${id} not found`);
	return node;
}

function channelNodes(graph: ReturnType<typeof buildGraph>): ChannelNode[] {
	return graph.nodes.filter((n): n is ChannelNode => n.kind === 'channel');
}

function weightedEdges(graph: ReturnType<typeof buildGraph>): WeightedEdge[] {
	return graph.edges.filter((e): e is WeightedEdge => e.kind === 'weighted');
}

describe('getChannelGraph', () => {
	it('computes the DCG weight correctly at known positions', () => {
		const graph = buildGraph();
		// seed1/chA: raw_position 1 (weight 1.00) + raw_position 3 (weight 0.50) = 1.50
		const chAEdge = weightedEdges(graph).find((e) => e.source === 'seed1' && e.target === 'chA');
		expect(chAEdge).toBeDefined();
		expect(chAEdge!.weight).toBeCloseTo(1.0 + 0.5, 10);

		// seed1/chB: raw_position 7 -> weight 1/log2(8) = 1/3
		const chBEdge = weightedEdges(graph).find((e) => e.source === 'seed1' && e.target === 'chB');
		expect(chBEdge).toBeDefined();
		expect(chBEdge!.weight).toBeCloseTo(1 / 3, 10);
	});

	it('credits only the position-0 channel; others become zero-weight collaborator edges', () => {
		const graph = buildGraph();
		const chC = findChannel(channelNodes(graph), 'chC');
		expect(chC.totalWeight).toBe(0);

		const collaboratorEdge = graph.edges.find(
			(e) => e.kind === 'collaborator' && e.source === 'seed1' && e.target === 'chC'
		);
		expect(collaboratorEdge).toBeDefined();

		const weightedChC = weightedEdges(graph).find((e) => e.target === 'chC');
		expect(weightedChC).toBeUndefined();
	});

	it('keeps a collaborator-only channel as a node with total weight zero', () => {
		const graph = buildGraph();
		const chC = findChannel(channelNodes(graph), 'chC');
		expect(chC.totalWeight).toBe(0);
		expect(chC.totalCredits).toBe(0);
	});

	it('merges several recommendations crediting the same channel in one seed into a single edge', () => {
		const graph = buildGraph();
		const chAEdges = weightedEdges(graph).filter((e) => e.source === 'seed1' && e.target === 'chA');
		expect(chAEdges).toHaveLength(1);
		expect(chAEdges[0]!.credits).toBe(2);
		expect(chAEdges[0]!.weight).toBeCloseTo(1.5, 10);
	});

	it('resolves a pair credited both at position 0 and as a collaborator to one weighted edge and no dashed edge', () => {
		const graph = buildGraph();
		const chFWeighted = weightedEdges(graph).filter((e) => e.source === 'seed1' && e.target === 'chF');
		expect(chFWeighted).toHaveLength(1);
		expect(chFWeighted[0]!.credits).toBe(1);
		expect(chFWeighted[0]!.weight).toBeCloseTo(1 / Math.log2(1 + 2), 10);

		const chFDashed = graph.edges.find(
			(e) => e.kind === 'collaborator' && e.source === 'seed1' && e.target === 'chF'
		);
		expect(chFDashed).toBeUndefined();
	});

	it('isolates seeds: a channel appearing in two seeds yields two distinct edges', () => {
		const graph = buildGraph();
		const chAEdges = weightedEdges(graph).filter((e) => e.target === 'chA');
		expect(chAEdges).toHaveLength(2);
		expect(new Set(chAEdges.map((e) => e.source))).toEqual(new Set(['seed1', 'seed2']));
	});

	it('gives a completed video with no recommendations an isolated seed node', () => {
		const graph = buildGraph();
		const seed3 = graph.nodes.find((n) => n.kind === 'seed' && n.id === 'seed3');
		expect(seed3).toBeDefined();
		expect(seed3!.kind === 'seed' && seed3!.hasRecommendations).toBe(false);

		const seed3Edges = graph.edges.filter((e) => e.source === 'seed3');
		expect(seed3Edges).toHaveLength(0);
	});

	it('never treats a failed video as a seed', () => {
		const graph = buildGraph();
		const failedNode = graph.nodes.find((n) => n.kind === 'seed' && n.id === 'failed1');
		expect(failedNode).toBeUndefined();
	});

	it('carries credit counts and weighted scores independently, never one derived from the other at render time', () => {
		const graph = buildGraph();
		const chAEdge = weightedEdges(graph).find((e) => e.source === 'seed1' && e.target === 'chA')!;
		// credits (2) and weight (1.5) are both present and neither equals the other
		expect(chAEdge.credits).toBe(2);
		expect(chAEdge.weight).not.toBe(chAEdge.credits);

		const chA = findChannel(channelNodes(graph), 'chA');
		expect(chA.totalCredits).toBe(3); // 2 from seed1 + 1 from seed2
		expect(chA.totalWeight).toBeCloseTo(1.5 + 1.0, 10);
	});
});
