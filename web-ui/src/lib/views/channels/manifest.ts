import type { ChannelGraph } from '$lib/server/queries/channelGraph';
import type { ViewManifest } from '../types';
import ChannelsView from './ChannelsView.svelte';

export const manifest: ViewManifest<ChannelGraph> = {
	id: 'channels',
	title: 'Channel graph',
	question: 'Who is recommended how much, and in whose videos?',
	load: async (db) => {
		const { getChannelGraph } = await import('$lib/server/queries/channelGraph');
		return getChannelGraph(db);
	},
	component: ChannelsView
};

export default manifest;
