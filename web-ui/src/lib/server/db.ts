import Database from 'better-sqlite3';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));

// Default is relative to this file, not to process.cwd(), so the web-ui makes no
// assumption about where it's invoked from. src/lib/server -> web-ui -> repo root -> data.
const defaultDbPath = resolve(here, '../../../../data/db.sqlite3');

const dbPath = process.env['SCRAPER_DB_PATH'] ?? defaultDbPath;

// fileMustExist is not optional: without it, better-sqlite3 silently creates an empty
// database on a mistyped path, and every view then renders an empty graph that looks
// like a data problem rather than a configuration one.
export const db = new Database(dbPath, { readonly: true, fileMustExist: true });
