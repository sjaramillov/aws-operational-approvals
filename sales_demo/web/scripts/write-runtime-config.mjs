import { chmod, mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const destination = process.argv[2]
if (!destination) {
  throw new Error('Usage: node scripts/write-runtime-config.mjs <output-path|--check> < terraform-output.json')
}

process.stdin.setEncoding('utf8')
let raw = ''
for await (const chunk of process.stdin) raw += chunk
const parsed = JSON.parse(raw)
const source = parsed?.frontend_runtime_config?.value ?? parsed
const required = [
  'apiBaseUrl',
  'awsRegion',
  'cognitoUserPool',
  'cognitoClientId',
  'cognitoDomain',
  'redirectUri',
  'logoutUri',
  'oauthFlow',
  'scopes',
  'expiresAt',
  'syntheticDataOnly',
  'deploymentBinding',
]
for (const key of required) {
  if (!(key in source)) throw new Error(`Terraform output is missing ${key}.`)
}
if (
  source.oauthFlow !== 'authorization_code_pkce'
  || source.syntheticDataOnly !== true
  || !Array.isArray(source.scopes)
  || source.scopes.length !== 1
  || source.scopes[0] !== 'openid'
  || typeof source.deploymentBinding !== 'object'
  || source.deploymentBinding === null
  || Object.keys(source.deploymentBinding).length !== 2
  || !Object.hasOwn(source.deploymentBinding, 'sourceRevision')
  || !Object.hasOwn(source.deploymentBinding, 'frontendReleaseSha256')
  || !/^[0-9a-f]{40}$/.test(source.deploymentBinding.sourceRevision)
  || !/^[0-9a-f]{64}$/.test(source.deploymentBinding.frontendReleaseSha256)
  || (source.expiresAt !== null
    && (typeof source.expiresAt !== 'string'
      || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(source.expiresAt)))
) {
  throw new Error('Terraform output violates the public pilot contract.')
}

const config = {
  apiBaseUrl: String(source.apiBaseUrl),
  awsRegion: String(source.awsRegion),
  cognitoUserPool: String(source.cognitoUserPool),
  cognitoClientId: String(source.cognitoClientId),
  cognitoDomain: String(source.cognitoDomain),
  redirectUri: String(source.redirectUri),
  logoutUri: String(source.logoutUri),
  oauthFlow: 'authorization_code_pkce',
  scopes: ['openid'],
  expiresAt: source.expiresAt === null ? null : String(source.expiresAt),
  syntheticDataOnly: true,
  deploymentBinding: {
    sourceRevision: String(source.deploymentBinding.sourceRevision),
    frontendReleaseSha256: String(source.deploymentBinding.frontendReleaseSha256),
  },
}

if (destination === '--check') {
  if (config.expiresAt !== source.expiresAt) throw new Error('PREPARED null expiry was not preserved.')
  process.stdout.write('runtime-config post-apply contract: OK\n')
} else {
  const output = resolve(destination)
  const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const publicDist = resolve(webRoot, 'dist')
  if (output === publicDist || output.startsWith(publicDist + sep)) {
    throw new Error('runtime-config.json must remain outside web/dist and be passed separately to the publisher.')
  }
  await mkdir(dirname(output), { recursive: true })
  await writeFile(output, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 })
  await chmod(output, 0o600)
}
