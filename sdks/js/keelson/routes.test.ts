import {
    constructRouteEditionKey, constructRouteLatestKey, constructRouteChangeEventKey, constructRouteSignatureKey,
    constructRouteStatusKey, parseRouteKey, editionConflict, bumpEdition, editionSigningInput,
    mergeWaypointDefaults, LeaseTracker, publishEditionBump, resolveRouteRef,
    EditionConflictError, DanglingRouteRefError, uncover,
} from './index';
import { Route, RouteRef, RouteTopology } from './payloads/Route';
import { RouteChangeEvent } from './payloads/RouteChangeEvent';
import { RouteEditAuthority } from './payloads/RouteEditAuthority';

const BASE = 'rise';
const ENTITY = 'sf18';
const NOW = new Date('2026-11-06T12:00:00Z');

function route(edition = 3, n = 3): Route {
    return Route.fromPartial({
        routeId: 'r7',
        routeEditionNumber: edition,
        waypoints: { waypoint: Array.from({ length: n }, (_, i) => ({ id: `wp${i}` })) },
    });
}

describe('route keys (§6.3)', () => {
    it('builds the edition store keys', () => {
        expect(constructRouteEditionKey(BASE, ENTITY, 'r7', 4)).toBe('rise/@v0/sf18/pubsub/route/r7/edition/4');
        expect(constructRouteLatestKey(BASE, ENTITY, 'r7')).toBe('rise/@v0/sf18/pubsub/route/r7/latest');
        expect(constructRouteChangeEventKey(BASE, ENTITY, 'r7', 'c1')).toBe('rise/@v0/sf18/pubsub/route_change_event/r7/c1');
        expect(constructRouteSignatureKey(BASE, ENTITY, 'r7', 4)).toBe('rise/@v0/sf18/pubsub/route_signature/r7/edition/4');
        expect(constructRouteStatusKey(BASE, ENTITY, 'r7')).toBe('rise/@v0/sf18/pubsub/route_status/r7');
    });
    it('refuses a composite route id and a non-positive edition', () => {
        expect(() => constructRouteLatestKey(BASE, ENTITY, 'a/b')).toThrow();
        expect(() => constructRouteEditionKey(BASE, ENTITY, 'r7', 0)).toThrow();
        expect(() => constructRouteEditionKey(BASE, ENTITY, 'r7', 1.5)).toThrow();
    });
    it('parses editions and latest, and nothing else', () => {
        expect(parseRouteKey(constructRouteEditionKey(BASE, ENTITY, 'r7', 4)))
            .toEqual({ basePath: BASE, entityId: ENTITY, routeId: 'r7', kind: 'edition', edition: 4 });
        expect(parseRouteKey(constructRouteLatestKey(BASE, ENTITY, 'r7')))
            .toEqual({ basePath: BASE, entityId: ENTITY, routeId: 'r7', kind: 'latest' });
        for (const k of [
            'rise/@v0/sf18/pubsub/route/navigator',
            'rise/@v0/sf18/pubsub/route/r7/edition/0',
            'rise/@v0/sf18/pubsub/route/r7/latest/extra',
            'rise/@v0/sf18/pubsub/route_status/r7',
        ]) {
            expect(parseRouteKey(k)).toBeUndefined();
        }
    });
});

describe('the edition bump (§6.4)', () => {
    it.each([
        [3, 3, false, undefined],
        [0, undefined, false, undefined],
        [3, undefined, false, undefined],
        [3, 3, true, 'next-exists'],
        [3, 4, false, 'latest-moved'],
        [3, 2, false, 'latest-moved'],
    ])('editionConflict(%s, %s, %s) = %s', (local, latest, next, kind) => {
        expect(editionConflict(local as number, latest as number | undefined, next as boolean)?.kind).toBe(kind);
    });

    it('publishes the edition, then latest, then the change event', () => {
        const r = route(3);
        const bump = bumpEdition(BASE, ENTITY, r, { authorId: 'anna@ROC-1', authorSite: 'ROC-1', reason: 'weather', now: NOW, changeId: 'c-9' });
        expect(bump.publications.map(([k]) => k)).toEqual([
            'rise/@v0/sf18/pubsub/route/r7/edition/4',
            'rise/@v0/sf18/pubsub/route/r7/latest',
            'rise/@v0/sf18/pubsub/route_change_event/r7/c-9',
        ]);
        expect(r.routeEditionNumber).toBe(3);
        expect(bump.route.routeEditionNumber).toBe(4);
        expect(bump.publications[0][1]).toEqual(bump.publications[1][1]);
        expect(Route.decode(uncover(bump.publications[0][1])![2]).routeEditionNumber).toBe(4);
        const event = RouteChangeEvent.decode(uncover(bump.publications[2][1])![2]);
        expect([event.oldEdition, event.newEdition, event.authorSite]).toEqual([3, 4, 'ROC-1']);
    });

    it('signs over the stored payload bytes', () => {
        const bump = bumpEdition(BASE, ENTITY, route(), { authorId: 'a', now: NOW });
        expect(editionSigningInput(bump.publications[0][1])).toEqual(Route.encode(bump.route).finish());
    });
});

describe('waypoint defaults (§6.2)', () => {
    it('a present value wins, even 0; an absent one takes the default', () => {
        const r = route(3, 3);
        r.waypoints!.defaults = { $type: 'keelson.DefaultWaypoint', leg: undefined, radiusM: 50, plannedSogKnots: 8 };
        r.waypoints!.waypoint[0].radiusM = 0;
        r.waypoints!.waypoint[1].plannedSogKnots = 5;
        const m = mergeWaypointDefaults(r);
        expect(m.map(w => w.radiusM)).toEqual([0, 50, 50]);
        expect(m.map(w => w.plannedSogKnots)).toEqual([8, 5, 8]);
        expect(r.waypoints!.waypoint[2].radiusM).toBeUndefined();
    });

    it('fills only outgoing legs of an open route, and the closing leg of a closed one', () => {
        const r = Route.fromPartial({
            routeId: 'r7',
            waypoints: {
                waypoint: [{ id: 'a' }, { id: 'b', leg: { xtdPortM: 10 } }, { id: 'c' }],
                defaults: { leg: { xtdPortM: 30, sogMaxKnots: 12, xtdCorridorMode: 1 } },
            },
        });
        const m = mergeWaypointDefaults(r);
        expect([m[0].leg?.xtdPortM, m[0].leg?.sogMaxKnots]).toEqual([30, 12]);
        expect([m[1].leg?.xtdPortM, m[1].leg?.sogMaxKnots]).toEqual([10, 12]);
        expect(m[2].leg).toBeUndefined();
        expect(m[0].leg?.xtdCorridorMode).toBe(0);
        r.waypoints!.topology = RouteTopology.ROUTE_TOPOLOGY_CLOSED;
        expect(mergeWaypointDefaults(r)[2].leg?.xtdPortM).toBe(30);
    });
});

describe('the lease (§6.5.1)', () => {
    const lease = (token: string, released = false, ttl = 30) =>
        RouteEditAuthority.fromPartial({ routeId: 'r7', token, released, leaseTtlSeconds: ttl, expiresAt: new Date('2000-01-01') });

    it('expires on the receiver clock, never on expiresAt', () => {
        let t = 1000;
        const tracker = new LeaseTracker(30, () => t);
        expect(tracker.observe(lease('t1'))).toBe(true);
        t += 29.9;
        expect(tracker.holder()).toBeDefined();
        t += 0.2;
        expect(tracker.expired()).toBe(true);
    });

    it('re-arms on a heartbeat; a foreign token waits for the lapse; only the current token releases', () => {
        let t = 0;
        const tracker = new LeaseTracker(30, () => t);
        tracker.observe(lease('t1'));
        t += 20;
        expect(tracker.observe(lease('t1'))).toBe(true);
        t += 20;
        expect(tracker.observe(lease('t2'))).toBe(false);
        expect(tracker.observe(lease('t9', true))).toBe(false);
        t += 31;
        expect(tracker.observe(lease('t2'))).toBe(true);
        expect(tracker.observe(lease('t2', true))).toBe(true);
        expect(tracker.holder()).toBeUndefined();
    });
});

describe('over a transport', () => {
    function memoryStore() {
        const data = new Map<string, Uint8Array>();
        const order: string[] = [];
        return {
            data, order,
            get: async (key: string) => (data.has(key) ? [data.get(key)!] : []),
            put: (key: string, payload: Uint8Array) => { data.set(key, payload); order.push(key); },
        };
    }

    it('publishes in order, then refuses an editor that fell behind', async () => {
        const s = memoryStore();
        const one = await publishEditionBump(s.get, s.put, BASE, ENTITY, route(0), { authorId: 'a', now: NOW });
        expect(s.order.map(k => k.split('/pubsub/')[1])).toEqual([
            'route/r7/edition/1', 'route/r7/latest', `route_change_event/r7/${one.changeEvent.changeId}`,
        ]);
        await publishEditionBump(s.get, s.put, BASE, ENTITY, one.route, { authorId: 'b', now: NOW });
        await expect(publishEditionBump(s.get, s.put, BASE, ENTITY, one.route, { authorId: 'c', now: NOW }))
            .rejects.toBeInstanceOf(EditionConflictError);
        expect(s.order).toHaveLength(6);
    });

    it('resolves a stored edition and surfaces a dangling one', async () => {
        const s = memoryStore();
        await publishEditionBump(s.get, s.put, BASE, ENTITY, route(0), { authorId: 'a', now: NOW });
        const ref = (n: number) => RouteRef.fromPartial({ routeId: 'r7', routeEditionNumber: n });
        const { route: got, envelope } = await resolveRouteRef(s.get, BASE, ENTITY, ref(1));
        expect(got.routeEditionNumber).toBe(1);
        expect(editionSigningInput(envelope)).toEqual(Route.encode(got).finish());
        await expect(resolveRouteRef(s.get, BASE, ENTITY, ref(2))).rejects.toBeInstanceOf(DanglingRouteRefError);
    });
});
