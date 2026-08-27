/**
 * Loads run artefacts. Prefers the RunPod results volume (x3n7kgbbit) and falls
 * back to a local copy under ../results/final.
 *
 * The market-data volume 8qik4zxpxq is NEVER touched here — the dashboard reads
 * only what the pipeline already wrote to the results volume.
 */
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3'
import { parquetReadObjects } from 'hyparquet'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(__dirname, '../../..')
const LOCAL = path.join(REPO, 'results', 'final')

const RUN_ID = process.env.RUN_ID || 'pso_lssvm_v1'
const RESULTS_VOLUME = process.env.RESULTS_VOLUME_ID || 'x3n7kgbbit'
const SOURCE_VOLUME = '8qik4zxpxq'
const PREFIX = `runs/${RUN_ID}/latest`

if (RESULTS_VOLUME === SOURCE_VOLUME) {
  throw new Error('RESULTS_VOLUME_ID must not be the read-only market-data volume')
}

function s3 () {
  if (!process.env.AWS_ACCESS_KEY_ID || !process.env.AWS_SECRET_ACCESS_KEY) return null
  return new S3Client({
    region: process.env.RUNPOD_S3_REGION || 'eu-ro-1',
    endpoint: process.env.RUNPOD_S3_ENDPOINT || 'https://s3api-eu-ro-1.runpod.io',
    credentials: {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
    },
    forcePathStyle: true,
  })
}

async function fromS3 (key) {
  const client = s3()
  if (!client) return null
  const res = await client.send(
    new GetObjectCommand({ Bucket: RESULTS_VOLUME, Key: `${PREFIX}/${key}` }))
  return Buffer.from(await res.Body.transformToByteArray())
}

async function fromDisk (key) {
  return fs.readFile(path.join(LOCAL, key))
}

/** Try the volume, then disk. Returns {buf, origin}. */
async function load (key) {
  try {
    const buf = await fromS3(key)
    if (buf) return { buf, origin: `s3://${RESULTS_VOLUME}/${PREFIX}/${key}` }
  } catch (err) {
    console.warn(`[store] S3 miss for ${key}: ${err.name || err.message}`)
  }
  const buf = await fromDisk(key)
  return { buf, origin: path.join(LOCAL, key) }
}

let cache = null
const TTL_MS = Number(process.env.CACHE_TTL_MS || 60_000)

export async function getData ({ refresh = false } = {}) {
  // Without a TTL the process would serve a snapshot forever — after the volume
  // was cleared and re-run, the API kept reporting the OLD row count while still
  // claiming an s3:// origin that no longer existed.
  const fresh = cache && Date.now() - Date.parse(cache.loadedAt) < TTL_MS
  if (fresh && !refresh) return cache

  const metricsRes = await load('metrics.json')
  const metrics = JSON.parse(metricsRes.buf.toString('utf8'))

  const predRes = await load('predictions.parquet')
  const ab = predRes.buf.buffer.slice(
    predRes.buf.byteOffset, predRes.buf.byteOffset + predRes.buf.byteLength)
  const rows = await parquetReadObjects({ file: ab })

  let next = []
  try {
    const nres = await load('next_session.parquet')
    const nab = nres.buf.buffer.slice(
      nres.buf.byteOffset, nres.buf.byteOffset + nres.buf.byteLength)
    next = await parquetReadObjects({ file: nab })
  } catch { /* live predictions are optional */ }

  // Normalise: parquet dates arrive as epoch ms (or Date) depending on writer.
  const norm = rows.map(r => ({
    date: toISO(r.date),
    ticker: r.ticker,
    pred: Number(r.pred_return),
    actual: Number(r.actual_return),
    gap: Number(r.gap_days ?? 1),
    source: r.source || 'backtest',
  })).filter(r => r.date && Number.isFinite(r.pred) && Number.isFinite(r.actual))
  norm.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0))

  cache = {
    metrics,
    rows: norm,
    next: next.map(r => ({
      ticker: r.ticker,
      forSession: toISO(r.for_session),
      asOf: toISO(r.as_of),
      lastClose: Number(r.last_close),
      pred: Number(r.pred_return),
      impliedClose: Number(r.implied_close),
    })),
    origin: { metrics: metricsRes.origin, predictions: predRes.origin },
    fromVolume: predRes.origin.startsWith('s3://'),
    loadedAt: new Date().toISOString(),
  }
  return cache
}

function toISO (v) {
  if (v == null) return null
  if (v instanceof Date) return v.toISOString().slice(0, 10)
  if (typeof v === 'bigint') return new Date(Number(v)).toISOString().slice(0, 10)
  if (typeof v === 'number') {
    const ms = v > 1e12 ? v : v > 1e9 ? v * 1000 : v * 86400000
    return new Date(ms).toISOString().slice(0, 10)
  }
  const s = String(v)
  return s.length >= 10 ? s.slice(0, 10) : null
}
