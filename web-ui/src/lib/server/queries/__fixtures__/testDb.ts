import Database from 'better-sqlite3';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));

// The schema is read from the scraper's authoritative source, never duplicated or
// modified here (web-ui/SPEC.md, Repository boundary).
const schemaPath = resolve(here, '../../../../../../src/scraper/storage/schema.sql');
const schemaSql = readFileSync(schemaPath, 'utf-8');

export interface FixtureVideo {
	video_id: string;
	status: 'completed' | 'failed';
}

export interface FixtureRecommendation {
	video_id: string;
	recommended_video_id: string;
	raw_position: number;
	channels: Array<{
		position: number;
		channel_id: string;
		name?: string | null;
		handle?: string | null;
	}>;
}

export interface Fixture {
	videos: FixtureVideo[];
	recommendations: FixtureRecommendation[];
}

/**
 * Builds a fresh in-memory database with the real schema, then inserts the given fixture
 * rows. Never touches data/db.sqlite3.
 */
export function createFixtureDb(fixture: Fixture): Database.Database {
	const db = new Database(':memory:');
	db.exec(schemaSql);

	const runId = 'run-1';
	db.prepare(`INSERT INTO runs (run_id, started_at) VALUES (?, ?)`).run(
		runId,
		'2026-01-01T00:00:00Z'
	);

	const insertVideo = db.prepare(`
		INSERT INTO videos (video_id, run_id, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?)
	`);
	for (const video of fixture.videos) {
		insertVideo.run(video.video_id, runId, video.status, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
	}

	const insertRecommendation = db.prepare(`
		INSERT INTO recommendations (
			video_id, run_id, recommended_video_id, raw_position, normalised_position,
			thumbnail_sources_json, animated_preview_sources_json
		) VALUES (?, ?, ?, ?, ?, '[]', '[]')
	`);
	const insertChannel = db.prepare(`
		INSERT INTO recommendation_channels (
			recommendation_id, position, channel_id, name, handle, avatar_sources_json
		) VALUES (?, ?, ?, ?, ?, '[]')
	`);

	for (const rec of fixture.recommendations) {
		const result = insertRecommendation.run(
			rec.video_id,
			runId,
			rec.recommended_video_id,
			rec.raw_position,
			rec.raw_position
		);
		const recommendationId = result.lastInsertRowid;
		for (const channel of rec.channels) {
			insertChannel.run(
				recommendationId,
				channel.position,
				channel.channel_id,
				channel.name ?? null,
				channel.handle ?? null
			);
		}
	}

	return db;
}
