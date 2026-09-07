import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadRuntimeConfig, parseRuntimeConfig } from './runtimeConfig'

const valid = {
  apiBaseUrl: 'https://a1b2c3d4.execute-api.us-east-1.amazonaws.com',
  awsRegion: 'us-east-1',
  cognitoUserPool: 'us-east-1_AbCdEf123',
  cognitoClientId: 'public-client-example',
  cognitoDomain: 'https://approvals-sales-pilot.auth.us-east-1.amazoncognito.com',
  redirectUri: 'https://d111111abcdef8.cloudfront.net/auth/callback',
  logoutUri: 'https://d111111abcdef8.cloudfront.net/',
  oauthFlow: 'authorization_code_pkce',
  scopes: ['openid'],
  expiresAt: '2026-09-03T18:00:00Z',
  syntheticDataOnly: true,
  deploymentBinding: {
    sourceRevision: '1111111111111111111111111111111111111111',
    frontendReleaseSha256: '2222222222222222222222222222222222222222222222222222222222222222',
  },
}

afterEach(() => vi.unstubAllGlobals())

describe('public runtime configuration', () => {
  it('accepts the exact post-apply AWS contract and normalizes URLs', () => {
    expect(parseRuntimeConfig(valid)).toMatchObject({
      apiBaseUrl: 'https://a1b2c3d4.execute-api.us-east-1.amazonaws.com',
      cognitoDomain: 'https://approvals-sales-pilot.auth.us-east-1.amazoncognito.com',
      scopes: ['openid'],
      syntheticDataOnly: true,
    })
  })

  it('accepts a null expiry only as the public PREPARED configuration', () => {
    expect(parseRuntimeConfig({ ...valid, expiresAt: null })).toMatchObject({
      expiresAt: null,
      syntheticDataOnly: true,
    })
  })

  it('rejects an unsupported Cognito scope or unexpected field', () => {
    expect(() => parseRuntimeConfig({ ...valid, scopes: ['openid', 'profile'] })).toThrow(/único scope permitido/)
    expect(() => parseRuntimeConfig({ ...valid, clientSecret: 'forbidden' })).toThrow(/contrato exacto/)
  })

  it.each([
    ['HTTP en API', { apiBaseUrl: 'http://a1b2c3d4.execute-api.us-east-1.amazonaws.com' }, /HTTPS/],
    ['región inválida', { awsRegion: 'east-1' }, /región AWS válida/],
    ['API fuera de API Gateway', { apiBaseUrl: 'https://api.example.invalid' }, /host regional de API Gateway/],
    ['API de otra región', { apiBaseUrl: 'https://a1b2c3.execute-api.eu-west-1.amazonaws.com' }, /host regional de API Gateway/],
    ['Cognito de otra región', { cognitoDomain: 'https://pilot.auth.eu-west-1.amazoncognito.com' }, /dominio regional de Cognito/],
    ['pool sin prefijo regional', { cognitoUserPool: 'eu-west-1_AbCdEf123' }, /prefijado por la región/],
    ['redirect ajeno a CloudFront', { redirectUri: 'https://app.example.invalid/auth/callback' }, /origen CloudFront/],
    ['orígenes distintos', { logoutUri: 'https://d222222abcdef8.cloudfront.net/' }, /mismo origen CloudFront/],
    ['callback incorrecto', { redirectUri: 'https://d111111abcdef8.cloudfront.net/callback' }, /auth\/callback/],
    ['binding incompleto', { deploymentBinding: { sourceRevision: '1111111111111111111111111111111111111111' } }, /hashes exactos/],
    ['hash de bundle inválido', { deploymentBinding: { ...valid.deploymentBinding, frontendReleaseSha256: 'not-a-hash' } }, /hashes exactos/],
  ])('rechaza %s', (_label, override, expected) => {
    expect(() => parseRuntimeConfig({ ...valid, ...override })).toThrow(expected as RegExp)
  })

  it('fails closed when the SPA fallback returns HTML instead of runtime JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<!doctype html><title>SPA</title>', {
          status: 200,
          headers: { 'Content-Type': 'text/html; charset=utf-8' },
        }),
      ),
    )

    await expect(loadRuntimeConfig()).rejects.toThrow(/fallback SPA no es una configuración válida/)
  })

  it('loads the post-apply JSON without cache and returns the validated contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(valid), {
        status: 200,
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(loadRuntimeConfig()).resolves.toMatchObject({
      apiBaseUrl: valid.apiBaseUrl,
      cognitoClientId: valid.cognitoClientId,
      scopes: ['openid'],
    })
    expect(fetchMock).toHaveBeenCalledWith('/runtime-config.json', {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    })
  })
})
