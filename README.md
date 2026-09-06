# Myatmyat

## Dashboard metrics

The Railway service exposes `GET /api/metrics` for the hosted Manus dashboard. Configure these Railway Variables (never commit their values):

```text
METRICS_TOKEN=<same token entered in the dashboard Settings page>
DASHBOARD_ORIGIN=https://stlinkdash-9eyizxud.manus.space
```

The endpoint requires `Authorization: Bearer <METRICS_TOKEN>` (or `X-Metrics-Token`) and returns JSON. Requests from any other browser origin are rejected. Railway must expose the service over HTTPS and provide its public URL to the dashboard as `<railway-public-url>/api/metrics`.
