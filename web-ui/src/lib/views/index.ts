import type { ViewManifest } from './types';

// Every visualization lives in its own folder here and exports a manifest.ts default.
// Adding a view means adding a folder - this glob is the only thing that ever needs to
// see it, and it never needs editing.
const modules = import.meta.glob<{ default: ViewManifest }>('./*/manifest.ts', {
	eager: true
});

export const views: ViewManifest[] = Object.values(modules)
	.map((mod) => mod.default)
	.sort((a, b) => a.id.localeCompare(b.id));

export function getView(id: string): ViewManifest | undefined {
	return views.find((view) => view.id === id);
}
