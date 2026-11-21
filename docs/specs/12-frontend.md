# 12. Frontend

## 1. Stack

`FE-1` Vite, React 19, TypeScript (strict), Tailwind 3, React Router 7, the existing SuwaPath design
tokens and UI kit (rebuilt from our earlier prototype into the structure below). Added libraries:
`@tanstack/react-query` (server state), `openapi-fetch` (typed client), `openapi-typescript` (dev,
type generation). Anything else needs a pull request that explains why.

## 2. Structure

```
frontend/src/
  app/              main.tsx, router.tsx (all routes, lazy loaded), providers.tsx, AppShell
  shared/
    ui/             design tokens, components.css, UI kit (Card, Chip, UrgencyBadge, Skeleton,
                    Empty, Notice, DataTable, Field, Avatar, SimulatedBadge, RulesOnlyBadge, ...)
    api/            openapi.json, openapi.d.ts (generated), client.ts, queryKeys.ts, errors.ts
    hooks/          useDebounce, useCaseStream, useMeStream, useMe, useInfiniteList
    i18n/           en.ts, si.ts, useT() (typed keys, no library)
    lib/            format (dates in Asia/Colombo), illustration
  features/
    auth/  companion/  cases/  record/  consent/  care/  booking/  reports/  imaging/
    guardian/  doctor/  hospital/  reviews/  notifications/  ops/  dev/
      each: api.ts (query and mutation hooks), components/, pages/
```

`FE-2` Feature folders mirror backend modules. A feature imports only `shared/` and its own folder.
Cross-feature UI goes into `shared/ui`.

`FE-3` Wave 0 creates `router.tsx` with **every route of section 4 already declared**, each pointing
to a placeholder page in its feature folder. Feature issues replace their own page files and never
edit the router or the shell navigation config.

## 3. Data layer rules

`FE-10` All server data goes through TanStack Query hooks in `features/*/api.ts`. No `fetch` or
`useEffect` data loading in components.

`FE-11` Query keys come from `shared/api/queryKeys.ts` (one factory per feature).

`FE-12` `useCaseStream(caseId)` and `useMeStream()` open SSE with a stream ticket, reconnect with
`Last-Event-ID` and exponential backoff, and on each event update or invalidate the matching
queries. Polling is not used where a stream exists.

`FE-13` Optimistic updates for: dose taken, alert acknowledged, notification read, doctor decision
(rolled back with a toast on error).

`FE-14` Search inputs debounce 300 ms (`useDebounce`), cancel stale requests (query keys include the
term), and show results progressively.

`FE-15` Lists use cursor pagination via `useInfiniteList` ("Show more" button, not infinite scroll,
for elderly users).

`FE-16` Every route is lazy loaded. Initial JavaScript for the login page stays under 200 KB gzip.

`FE-17` Every page has loading skeletons, an empty state and an error state with a retry button.
Each route has an error boundary.

`FE-18` Auth: access token in memory, refresh through the `HttpOnly` cookie (`SEC-4`). On 401 the
client refreshes once and retries; on failure it goes to login.

## 4. Routes by role

| Role | Routes |
|---|---|
| public | `/login` |
| patient | `/patient` (home: today's doses, next appointment, open case), `/patient/companion` and `/patient/companion/:caseId`, `/patient/record`, `/patient/sharing`, `/patient/medicines`, `/patient/appointments`, `/patient/find-care`, `/patient/reports`, `/patient/imaging`, `/patient/programmes`, `/patient/private`, `/patient/reviews`, `/patient/notifications`, `/patient/settings` |
| guardian | `/guardian` (dependents), `/guardian/dependents/:patientId`, `/guardian/alerts`, `/guardian/rules`, `/guardian/notifications`, `/guardian/settings` |
| doctor | `/doctor` (queue), `/doctor/cases/:caseId` (brief with sources, notes, request fact, refer to programme), `/doctor/approvals`, `/doctor/reviews`, `/doctor/notifications`, `/doctor/settings` |
| hospital_admin | `/hospital` (sessions), `/hospital/sessions/:sessionId`, `/hospital/offerings` |
| system_admin | `/admin` (AI operations), `/admin/programmes` (catalogue and offering approvals), `/admin/audit`, `/admin/budgets`, `/admin/dead-letters`, `/admin/flags` |
| dev and demo | `/dev` (clock, scenario reset), visible only when the API reports `env` is development or demo |

## 5. UX rules

`FE-20` Patient and guardian screens: base font 18 px, tap targets at least 48 px, WCAG AA contrast,
one primary action per screen, plain words, no medical jargon.

`FE-21` Language switch (සිංහල / English) is visible on every patient and guardian screen. All
static text comes from `i18n`. Agent replies arrive already in the patient's language.

`FE-22` Badges: "Simulated AI" on anything produced by the fake provider; "Checked by rules only"
when model verification did not run; "Unvalidated screening model" on imaging lines for doctors.

`FE-23` Patients never see model probabilities, imaging scores or diagnosis words (`SAF-20`).

`FE-24` Companion chat streams replies token by token, shows agent progress labels
("Checking how urgent this is...", "Finding doctors near you..."), and announces new messages with
`aria-live="polite"`.

`FE-25` The emergency card (1990) is full screen, red, with a large call button (`tel:1990`) and
cannot be dismissed by accident.

`FE-26` The doctor brief shows every line with a source chip; clicking it opens the source
(document page and highlighted line). Unverified lines are marked.

`FE-27` No em dashes or en dashes in any UI text (`scripts/check_text.py`).

`FE-28` Security rules from `20-security-and-guardrails.md` apply: the shared `SafeMarkdown`
component is the only way to render model text (`SG-13`), banned APIs are linted (`SG-14`), and
caches and tokens are cleared on logout (`SG-16`).

## 6. Simple mode for older users

`FE-40` **One design system, two densities.** Simple mode is not a separate app or a separate
design. It is a mode of the same design system (`data-mode="simple"` on the root) that swaps token
values and component size variants: same colours, typeface, icons, shapes and components, so the
product looks and feels the same everywhere.

`FE-41` Token changes in simple mode: base text 22 px (headings scale with it), tap targets at least
56 px with 16 px spacing, icon size 32 px, higher contrast text, one column, no hover-only actions,
no swipe-only gestures.

`FE-42` Layout in simple mode: the patient home is a grid of at most 4 large tiles, each an icon
**with a text label** (never icon alone): "Talk to SuwaPath", "My medicines", "My doctor visits",
"Emergency 1990" (always red, always present). One task per screen, a large Back button, confirm
every action in plain words, and a "Call my son" style tile when a guardian exists.

`FE-43` Who gets it: offered at first login to patients aged 60 or more (default on), switchable
any time in settings (`CUS` patient setting `display_mode`), and a guardian may suggest it
(`CUS-16`). The user can also choose text size (normal, large, extra large) independently.

`FE-44` Every screen used by patients is built from components that support both densities; a
feature is not done until its screen works in simple mode (checked in review).

## 7. Assets, images and caching

`FE-30` Brand assets come from the team's logo files: SVG favicon plus PNG 32, 180 (Apple touch),
192 and 512, and the PWA manifest icons. Generated once by a script, committed.

`FE-31` Illustrations and photos are WebP (quality 80), in two sizes for `srcset` (mobile and
desktop), with explicit `width` and `height` to avoid layout shift, and `loading="lazy"` below the
fold. Source images stay out of the bundle; a script converts them.

`FE-32` Vite emits hashed file names. Static assets are served with
`Cache-Control: public, max-age=31536000, immutable`; `index.html` with `no-cache`.

`FE-33` Server data caching is TanStack Query only, with explicit `staleTime` per query type
(reference data such as specialties: 1 hour; doctor search: 1 minute; case and alerts: live through
SSE). **No service worker or browser storage ever caches API responses**, because they contain
health data and devices are often shared.
