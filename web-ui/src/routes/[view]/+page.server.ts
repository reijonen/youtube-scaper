import { error } from '@sveltejs/kit';
import { getView } from '$lib/views';
import { db } from '$lib/server/db';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	const view = getView(params.view);
	if (!view) {
		error(404, `Unknown view: ${params.view}`);
	}
	return {
		viewId: view.id,
		viewData: await view.load(db)
	};
};
