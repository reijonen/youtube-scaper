<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import Graph from 'graphology';
	import forceAtlas2 from 'graphology-layout-forceatlas2';
	import type { default as Sigma } from 'sigma';
	import type { default as FA2Layout } from 'graphology-layout-forceatlas2/worker';
	import type { ChannelGraph } from '$lib/server/queries/channelGraph';

	let { data }: { data: ChannelGraph } = $props();

	let container: HTMLDivElement | undefined = $state();
	let sigmaInstance: Sigma | undefined;
	let layout: FA2Layout | undefined;
	let layoutTimer: ReturnType<typeof setTimeout> | undefined;

	// Sigma renders comfortably with default styles well past this size, but degrades
	// once every node also carries an image texture (SPEC.md, Stack). Past this count the
	// node reducer below drops image fills for plain colored circles.
	const IMAGE_NODE_THRESHOLD = 300;

	const SEED_SIZE = 9;
	const MIN_CHANNEL_SIZE = 4;
	const CHANNEL_SIZE_SCALE = 6;

	const SEED_COLOR = '#4b5563';
	const WEIGHTED_EDGE_COLOR = 'rgba(100, 100, 220, 0.55)';
	const COLLABORATOR_EDGE_COLOR = 'rgba(150, 150, 150, 0.25)';

	function nodeKey(kind: 'seed' | 'channel', id: string): string {
		return kind + ':' + id;
	}

	function hashString(value: string): number {
		let hash = 0;
		for (let i = 0; i < value.length; i++) {
			hash = (hash * 31 + value.charCodeAt(i)) | 0;
		}
		return Math.abs(hash);
	}

	function fallbackColor(label: string): string {
		const hue = hashString(label) % 360;
		return 'hsl(' + hue + ', 60%, 45%)';
	}

	function channelSize(totalWeight: number): number {
		return MIN_CHANNEL_SIZE + Math.sqrt(totalWeight) * CHANNEL_SIZE_SCALE;
	}

	function buildGraph(): Graph {
		const graph = new Graph();

		for (const node of data.nodes) {
			if (node.kind === 'seed') {
				graph.addNode(nodeKey('seed', node.id), {
					label: node.id,
					size: SEED_SIZE,
					color: SEED_COLOR,
					x: Math.random(),
					y: Math.random()
				});
			} else {
				graph.addNode(nodeKey('channel', node.id), {
					label: node.label,
					size: channelSize(node.totalWeight),
					color: fallbackColor(node.label),
					type: 'image',
					image: '/avatars/' + node.id,
					x: Math.random(),
					y: Math.random()
				});
			}
		}

		for (const edge of data.edges) {
			const source = nodeKey('seed', edge.source);
			const target = nodeKey('channel', edge.target);
			if (edge.kind === 'weighted') {
				graph.addEdge(source, target, {
					size: 1 + Math.min(edge.weight, 4),
					color: WEIGHTED_EDGE_COLOR,
					weight: edge.weight
				});
			} else {
				graph.addEdge(source, target, {
					size: 1,
					color: COLLABORATOR_EDGE_COLOR,
					weight: 0.1
				});
			}
		}

		return graph;
	}

	onMount(() => {
		if (!container) return;

		// Sigma, @sigma/node-image, and the FA2 worker supervisor all touch browser-only
		// globals (WebGL2RenderingContext, Worker) at module scope, which crashes
		// SvelteKit's SSR pass if imported statically. Loaded here instead, inside
		// onMount, which only ever runs in the browser.
		Promise.all([
			import('sigma'),
			import('@sigma/node-image'),
			import('graphology-layout-forceatlas2/worker')
		]).then(([{ default: Sigma }, { NodeImageProgram }, { default: FA2Layout }]) => {
			if (!container) return;

			const graph = buildGraph();

			sigmaInstance = new Sigma(graph, container, {
				nodeProgramClasses: { image: NodeImageProgram },
				defaultEdgeType: 'line',
				renderEdgeLabels: false,
				nodeReducer: (_node, attrs) => {
					if (attrs.type === 'image' && graph.order > IMAGE_NODE_THRESHOLD) {
						return { ...attrs, type: undefined };
					}
					return attrs;
				}
			});

			layout = new FA2Layout(graph, {
				settings: forceAtlas2.inferSettings(graph)
			});
			layout.start();
			// Let the layout settle, then stop - it never needs to run forever for a
			// static dataset like this.
			layoutTimer = setTimeout(() => {
				layout?.stop();
			}, 4000);
		});
	});

	onDestroy(() => {
		if (layoutTimer) clearTimeout(layoutTimer);
		layout?.kill();
		sigmaInstance?.kill();
	});
</script>

<div class="graph-container" bind:this={container}></div>

<style>
	.graph-container {
		width: 100%;
		height: 600px;
	}
</style>
