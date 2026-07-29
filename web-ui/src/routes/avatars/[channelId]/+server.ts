import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { db } from '$lib/server/db';
import type { RequestHandler } from './$types';

const here = dirname(fileURLToPath(import.meta.url));

// Four levels up from src/routes/avatars/[channelId] is web-ui/.
const cacheDir = resolve(here, '../../../../avatar-cache');

// YouTube channel ids and this repo's synthetic test ids are all plain alphanumerics
// plus underscore/hyphen. This arrives in the URL, so it is validated before it ever
// touches a filesystem path - unvalidated, it is a path traversal.
const CHANNEL_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

const EXTENSION_BY_MIME: Record<string, string> = {
	'image/jpeg': 'jpg',
	'image/png': 'png',
	'image/webp': 'webp',
	'image/gif': 'gif'
};

function cachedDataPath(channelId: string, extension: string): string {
	return join(cacheDir, channelId + '.' + extension);
}

function findCachedFile(channelId: string): { data: string; contentType: string } | undefined {
	for (const [mime, ext] of Object.entries(EXTENSION_BY_MIME)) {
		const path = cachedDataPath(channelId, ext);
		if (existsSync(path)) {
			return { data: path, contentType: mime };
		}
	}
	return undefined;
}

function hashString(value: string): number {
	let hash = 0;
	for (let i = 0; i < value.length; i++) {
		hash = (hash * 31 + value.charCodeAt(i)) | 0;
	}
	return Math.abs(hash);
}

function escapeXml(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

function fallbackSvg(label: string): string {
	const initial = escapeXml((label.trim().charAt(0) || '?').toUpperCase());
	const hue = hashString(label) % 360;
	const fill = 'hsl(' + hue + ', 60%, 45%)';
	return (
		'<svg xmlns="http://www.w3.org/2000/svg" width="68" height="68" viewBox="0 0 68 68">' +
		'<circle cx="34" cy="34" r="34" fill="' +
		fill +
		'" />' +
		'<text x="34" y="34" text-anchor="middle" dominant-baseline="central" ' +
		'font-family="sans-serif" font-size="30" fill="white">' +
		initial +
		'</text>' +
		'</svg>'
	);
}

function fallbackResponse(label: string): Response {
	return new Response(fallbackSvg(label), {
		headers: { 'content-type': 'image/svg+xml' }
	});
}

interface ChannelRow {
	channel_id: string;
	name: string | null;
	handle: string | null;
	avatar_sources_json: string;
}

interface AvatarSource {
	url?: string;
}

export const GET: RequestHandler = async ({ params }) => {
	const channelId = params.channelId;

	if (!CHANNEL_ID_PATTERN.test(channelId)) {
		return new Response('invalid channel id', { status: 400 });
	}

	const cached = findCachedFile(channelId);
	if (cached) {
		return new Response(readFileSync(cached.data), {
			headers: { 'content-type': cached.contentType }
		});
	}

	const row = db
		.prepare<
			[string],
			ChannelRow
		>(`SELECT channel_id, name, handle, avatar_sources_json FROM recommendation_channels WHERE channel_id = ? LIMIT 1`)
		.get(channelId);

	const label = row?.handle ?? row?.name ?? channelId;

	if (!row) {
		return fallbackResponse(label);
	}

	let sources: AvatarSource[];
	try {
		sources = JSON.parse(row.avatar_sources_json);
	} catch {
		sources = [];
	}

	const url = sources[0]?.url;
	if (!url) {
		return fallbackResponse(label);
	}

	let response: Response;
	try {
		response = await fetch(url);
	} catch {
		return fallbackResponse(label);
	}

	if (!response.ok) {
		return fallbackResponse(label);
	}

	const contentType = response.headers.get('content-type') ?? 'image/jpeg';
	const extension = EXTENSION_BY_MIME[contentType] ?? 'jpg';
	const bytes = Buffer.from(await response.arrayBuffer());

	mkdirSync(cacheDir, { recursive: true });
	writeFileSync(cachedDataPath(channelId, extension), bytes);

	return new Response(bytes, {
		headers: { 'content-type': EXTENSION_BY_MIME[contentType] ? contentType : 'image/jpeg' }
	});
};
