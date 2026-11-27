// Route helpers: the §6 choreography of the protocol specification as library
// code. The JavaScript twin of keelson.routes / keelson.scaffolding.routes in the
// Python SDK — same names (camelCase), same rules:
//
//   - the edition store rides in the source_id position
//     (`route/{route_id}/edition/{N}`, `route/{route_id}/latest`)   §6.3
//   - an edition bump is three publishes in a load-bearing order      §6.4
//   - a signature is verified over the stored Envelope.payload bytes  §6.3.1
//   - a lease expires on the receiver's own clock                     §6.5.1
//   - waypoint defaults are merged by field presence                  §6.2
//
// This SDK has no zenoh dependency, so the two helpers that touch the bus
// (publishEditionBump, resolveRouteRef) take the transport as a `get` and a
// `put` you supply.

import { construct_pubSub_key, parse_pubsub_key, enclose, uncover } from './index';
import { Envelope } from './Envelope';
import { Route, RouteRef, Waypoint, Leg, RouteTopology } from './payloads/Route';
import { RouteChangeEvent } from './payloads/RouteChangeEvent';
import { RouteEditAuthority } from './payloads/RouteEditAuthority';

export const EDITION = 'edition';
export const LATEST = 'latest';

// ---------------------------------------------------------------- keys (§6.3)

function chunk(name: string, value: string): string {
    // A route id or change id is exactly one key chunk: a composite id publishes
    // without error and is never matched by the storage's `.../route/*/...`.
    if (typeof value !== 'string' || !value || value.includes('/') || value.includes('*')) {
        throw new Error(`${name} must be one non-empty key chunk, got ${JSON.stringify(value)}`);
    }
    return value;
}

function edition(n: number): number {
    if (!Number.isInteger(n) || n < 1) {
        throw new Error(`an edition is a positive integer, got ${n}`);
    }
    return n;
}

export function constructRouteEditionKey(basePath: string, entityId: string, routeId: string, n: number): string {
    return construct_pubSub_key(basePath, entityId, 'route', `${chunk('routeId', routeId)}/${EDITION}/${edition(n)}`);
}

export function constructRouteLatestKey(basePath: string, entityId: string, routeId: string): string {
    return construct_pubSub_key(basePath, entityId, 'route', `${chunk('routeId', routeId)}/${LATEST}`);
}

export function constructRouteChangeEventKey(basePath: string, entityId: string, routeId: string, changeId: string): string {
    return construct_pubSub_key(basePath, entityId, 'route_change_event', `${chunk('routeId', routeId)}/${chunk('changeId', changeId)}`);
}

export function constructRouteSignatureKey(basePath: string, entityId: string, routeId: string, n: number): string {
    return construct_pubSub_key(basePath, entityId, 'route_signature', `${chunk('routeId', routeId)}/${EDITION}/${edition(n)}`);
}

export function constructRouteStatusKey(basePath: string, entityId: string, routeId: string): string {
    return construct_pubSub_key(basePath, entityId, 'route_status', chunk('routeId', routeId));
}

export function constructRouteEditAuthorityKey(basePath: string, entityId: string, routeId: string): string {
    return construct_pubSub_key(basePath, entityId, 'route_edit_authority', chunk('routeId', routeId));
}

export function constructRouteEditRequestKey(basePath: string, entityId: string, routeId: string): string {
    return construct_pubSub_key(basePath, entityId, 'route_edit_request', chunk('routeId', routeId));
}

export interface RouteKey {
    basePath: string;
    entityId: string;
    routeId: string;
    kind: 'edition' | 'latest';
    edition?: number;
}

// `route/{id}/edition/{N}` or `route/{id}/latest`; undefined for any other key —
// another subject, or a `route` key of another shape (a declared route at
// `route/{source}` has no editions).
export function parseRouteKey(key: string): RouteKey | undefined {
    let parsed;
    try {
        parsed = parse_pubsub_key(key);
    } catch {
        return undefined;
    }
    if (!parsed || parsed.subject !== 'route' || parsed.targetId) return undefined;
    const parts = (parsed.sourceId ?? '').split('/');
    if (parts.length === 2 && parts[0] && parts[1] === LATEST) {
        return { basePath: parsed.base_path, entityId: parsed.entityId, routeId: parts[0], kind: 'latest' };
    }
    if (parts.length === 3 && parts[0] && parts[1] === EDITION && /^\d+$/.test(parts[2])) {
        const n = Number(parts[2]);
        if (n >= 1) {
            return { basePath: parsed.base_path, entityId: parsed.entityId, routeId: parts[0], kind: 'edition', edition: n };
        }
    }
    return undefined;
}

// ------------------------------------------------------- the bump (§6.4)

export interface Conflict {
    kind: 'next-exists' | 'latest-moved';
    localEdition: number;
    storedLatest?: number;
    message: string;
}

// Whether bumping `localEdition` is safe, from the store: `next-exists` when
// edition N+1 is already stored, `latest-moved` when the stored latest is not N
// (behind as well as ahead). No stored latest is clean.
export function editionConflict(localEdition: number, storedLatest: number | undefined, storedNextExists: boolean): Conflict | undefined {
    if (storedNextExists) {
        return { kind: 'next-exists', localEdition, storedLatest, message: `edition ${localEdition + 1} is already stored; reload before editing` };
    }
    if (storedLatest !== undefined && storedLatest !== localEdition) {
        return { kind: 'latest-moved', localEdition, storedLatest, message: `the stored latest is edition ${storedLatest}, not ${localEdition}` };
    }
    return undefined;
}

export interface EditionBump {
    route: Route;
    changeEvent: RouteChangeEvent;
    // [key, Envelope bytes], in the order they MUST be published.
    publications: Array<[string, Uint8Array]>;
}

export interface BumpOptions {
    authorId: string;
    authorSite?: string;
    reason?: string;
    summary?: string;
    now?: Date;
    changeId?: string;
}

function newChangeId(): string {
    const c: any = (globalThis as any).crypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    return require('crypto').randomUUID();
}

function envelopeBytes(payload: Uint8Array, at: Date): Uint8Array {
    return Envelope.encode(enclose(payload, at)).finish();
}

// The next edition of `route` and the three publications that publish it, in
// order (§6.4): the edition, then `latest`, then the change event. The edition
// must exist before `latest` points at it. `route` is not modified.
export function bumpEdition(basePath: string, entityId: string, route: Route, opts: BumpOptions): EditionBump {
    const now = opts.now ?? new Date();
    const old = route.routeEditionNumber;
    const next: Route = Route.fromPartial({ ...route, routeEditionNumber: old + 1, modifiedAt: now });
    const changeEvent: RouteChangeEvent = RouteChangeEvent.fromPartial({
        changeId: chunk('changeId', opts.changeId ?? newChangeId()),
        routeId: next.routeId,
        oldEdition: old,
        newEdition: old + 1,
        at: now,
        authorId: opts.authorId,
        authorSite: opts.authorSite ?? '',
        reason: opts.reason ?? 'edit',
        changeSummary: opts.summary ?? '',
    });
    const routeBytes = envelopeBytes(Route.encode(next).finish(), now);
    return {
        route: next,
        changeEvent,
        publications: [
            [constructRouteEditionKey(basePath, entityId, next.routeId, next.routeEditionNumber), routeBytes],
            [constructRouteLatestKey(basePath, entityId, next.routeId), routeBytes],
            [constructRouteChangeEventKey(basePath, entityId, next.routeId, changeEvent.changeId),
                envelopeBytes(RouteChangeEvent.encode(changeEvent).finish(), now)],
        ],
    };
}

// ------------------------------------------------------- signatures (§6.3.1)

// The bytes a route signature signs: exactly the Envelope.payload stored at
// `route/{id}/edition/{N}`. Never re-encode a decoded Route to verify: protobuf
// has no canonical encoding, so a round trip is not byte-stable.
export function editionSigningInput(storedEnvelope: Uint8Array): Uint8Array {
    return Envelope.decode(storedEnvelope).payload;
}

// --------------------------------------------------- waypoint defaults (§6.2)

// The optional scalar fields of Leg: those ts-proto leaves undefined in an empty
// Leg. xtdCorridorMode and the boundary lists are not optional, so they never
// come from the defaults (§6.2.2).
const LEG_DEFAULTABLE: string[] = Object.entries(Leg.fromPartial({}))
    .filter(([k, v]) => k !== '$type' && v === undefined && k !== 'extensions')
    .map(([k]) => k);

// A copy of each waypoint with the route's defaults applied. A value present on
// the waypoint wins — even 0. An absent radiusM / plannedSogKnots takes the
// default; an absent optional leg scalar takes defaults.leg's, but only on a
// waypoint that has an outgoing leg (all but the last on an open route, all on a
// closed one). The route is not modified.
export function mergeWaypointDefaults(route: Route): Waypoint[] {
    const wps = route.waypoints?.waypoint ?? [];
    const defaults = route.waypoints?.defaults;
    const closed = route.waypoints?.topology === RouteTopology.ROUTE_TOPOLOGY_CLOSED;
    return wps.map((wp, i) => {
        const m: Waypoint = Waypoint.fromPartial(wp);
        if (!defaults) return m;
        if (m.radiusM === undefined && defaults.radiusM !== undefined) m.radiusM = defaults.radiusM;
        if (m.plannedSogKnots === undefined && defaults.plannedSogKnots !== undefined) m.plannedSogKnots = defaults.plannedSogKnots;
        const outgoing = closed || i < wps.length - 1;
        if (outgoing && defaults.leg) {
            const dleg: any = defaults.leg;
            const additions: Record<string, unknown> = {};
            for (const k of LEG_DEFAULTABLE) {
                if (dleg[k] !== undefined && (m.leg as any)?.[k] === undefined) additions[k] = dleg[k];
            }
            if (Object.keys(additions).length) m.leg = Leg.fromPartial({ ...(m.leg ?? {}), ...additions });
        }
        return m;
    });
}

// ----------------------------------------------------------- the lease (§6.5.1)

type LeaseRecord = Pick<RouteEditAuthority, 'token' | 'released' | 'leaseTtlSeconds'>;

// A receiver's view of one lease (route_edit_authority, or any record with a
// token, released and leaseTtlSeconds). Expiry is decided on THIS clock: every
// accepted message arms `deadline = clock() + leaseTtlSeconds`; expiresAt and
// grantedAt are the holder's clock and are never compared (§6.5.1).
//   - a release with the current token clears it (any other token: ignored);
//   - a heartbeat with the current token re-arms it;
//   - another token is taken only when none is held or the held one has lapsed.
export class LeaseTracker<T extends LeaseRecord = RouteEditAuthority> {
    private record?: T;
    private deadline?: number;

    constructor(private defaultTtlSeconds = 30, private clock: () => number = () => performance.now() / 1000) {}

    observe(record: T): boolean {
        const now = this.clock();
        const held = this.record !== undefined && !this.lapsed(now);
        if (record.released) {
            if (this.record !== undefined && record.token === this.record.token) {
                this.record = undefined;
                this.deadline = undefined;
                return true;
            }
            return false;
        }
        if (held && record.token !== this.record!.token) return false;
        this.record = record;
        this.deadline = now + (record.leaseTtlSeconds || this.defaultTtlSeconds);
        return true;
    }

    private lapsed(now: number): boolean {
        return this.deadline === undefined || now > this.deadline;
    }

    holder(): T | undefined {
        if (this.record === undefined || this.lapsed(this.clock())) return undefined;
        return this.record;
    }

    expired(): boolean {
        return this.holder() === undefined;
    }

    remaining(): number | undefined {
        return this.holder() === undefined ? undefined : this.deadline! - this.clock();
    }
}

// --------------------------------------------------------- over a transport

// Every stored value under `key` (Envelope bytes): what your zenoh `get` returns.
export type Get = (key: string) => Promise<Uint8Array[]>;
export type Put = (key: string, payload: Uint8Array) => Promise<void> | void;

export class EditionConflictError extends Error {
    constructor(public conflict: Conflict) {
        super(conflict.message);
        this.name = 'EditionConflictError';
    }
}

export class DanglingRouteRefError extends Error {
    constructor(message: string) {
        super(message);
        this.name = 'DanglingRouteRefError';
    }
}

function decodeRoute(envelope: Uint8Array): Route {
    const u = uncover(envelope);
    if (!u) throw new Error('not a keelson Envelope');
    return Route.decode(u[2]);
}

// The edition stored at `route/{id}/latest` (the highest, if several stores
// answer), or undefined when none answers.
export async function readStoredEdition(get: Get, basePath: string, entityId: string, routeId: string): Promise<number | undefined> {
    const editions = (await get(constructRouteLatestKey(basePath, entityId, routeId))).map(e => decodeRoute(e).routeEditionNumber);
    return editions.length ? Math.max(...editions) : undefined;
}

// Read the store, refuse on a conflict (publishing nothing), then publish the
// edition, latest and the change event, in that order.
export async function publishEditionBump(
    get: Get, put: Put, basePath: string, entityId: string, route: Route, opts: BumpOptions & { check?: boolean },
): Promise<EditionBump> {
    if (opts.check !== false) {
        const latest = await readStoredEdition(get, basePath, entityId, route.routeId);
        const next = await get(constructRouteEditionKey(basePath, entityId, route.routeId, route.routeEditionNumber + 1));
        const conflict = editionConflict(route.routeEditionNumber, latest, next.length > 0);
        if (conflict) throw new EditionConflictError(conflict);
    }
    const bump = bumpEdition(basePath, entityId, route, opts);
    for (const [key, payload] of bump.publications) await put(key, payload);
    return bump;
}

// Fetch the edition a RouteRef names. Returns the route and the stored Envelope
// bytes (for editionSigningInput). Throws DanglingRouteRefError when it is not
// stored — never falls back to latest (§6.2).
export async function resolveRouteRef(get: Get, basePath: string, entityId: string, ref: RouteRef): Promise<{ route: Route; envelope: Uint8Array }> {
    if (!ref.routeId || ref.routeEditionNumber < 1) throw new DanglingRouteRefError(`not a resolvable RouteRef: ${JSON.stringify(ref)}`);
    const key = constructRouteEditionKey(basePath, entityId, ref.routeId, ref.routeEditionNumber);
    for (const envelope of await get(key)) {
        const route = decodeRoute(envelope);
        if (route.routeId === ref.routeId && route.routeEditionNumber === ref.routeEditionNumber) return { route, envelope };
    }
    throw new DanglingRouteRefError(`route ${ref.routeId} edition ${ref.routeEditionNumber} is not stored`);
}
