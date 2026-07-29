import type { Database } from 'better-sqlite3';
import type { Component } from 'svelte';

export interface ViewManifest<T = unknown> {
	id: string;
	title: string;
	question: string;
	load: (db: Database) => T | Promise<T>;
	component: Component<{ data: T }>;
}
