# Plan: Add Vertex AI and Bedrock Provider Support to `ant` CLI

## Summary

Add `--provider` (anthropic|vertex|bedrock), `--region`, and `--project-id` global flags to the `ant` CLI so it can route API calls through Google Vertex AI or Amazon Bedrock instead of the direct Anthropic API. The Go SDK already has full middleware support for both providers; we just need to wire it into the CLI.

**Key architectural decision:** Use a `Before` hook on the root command to resolve provider options into a package-level variable. `getDefaultRequestOptions()` appends them without signature changes. This avoids modifying all 34 code-generated handler files (which would be reverted by the next Stainless codegen run). Only 2 hand-maintained files need changes: `cmd.go` (flags + Before hook) and `cmdutil.go` (provider logic).

## Files to Modify

### 1. `pkg/cmd/cmd.go` — Add global flags + Before hook

Add 3 new flags after the existing `auth-token` flag:
- `--provider` (env: ANTHROPIC_PROVIDER, default: "anthropic", validated)
- `--region` (env: CLOUD_REGION)
- `--project-id` (env: GOOGLE_CLOUD_PROJECT)

Add a `Before` hook on the root command that calls `resolveProviderOptions(cmd)` and stores the result in a package-level `providerOptions` variable.

### 2. `pkg/cmd/cmdutil.go` — Provider routing logic

Add imports for `context`, `vertex`, `bedrock`, and `config` packages.

Add package-level var: `var providerOptions []option.RequestOption`

Add `resolveProviderOptions(cmd)` that switches on provider:
- "anthropic" → no-op
- "vertex" → validate region + project-id, call `safeVertexAuth(region, projectID)`
- "bedrock" → validate region (fallback to AWS_REGION/AWS_DEFAULT_REGION), call `safeBedrockAuth(region)`

Add panic-safe wrappers (`safeVertexAuth`, `safeBedrockAuth`) using `defer recover()` because the SDK's `WithGoogleAuth` and `WithLoadDefaultConfig` panic on credential failures.

Modify `getDefaultRequestOptions` to append `providerOptions...` and warn if `--api-key`/`--base-url` are set with non-anthropic providers.

### 3. `go.mod` — Add dependencies

Run `go get` for vertex/bedrock SDK packages. Use temporary `go 1.24.7` workaround for building.

## Implementation Order

1. Create git worktree from `next` branch for Go source modifications
2. Edit `pkg/cmd/cmd.go` — add 3 flags + Before hook
3. Edit `pkg/cmd/cmdutil.go` — add provider resolution, panic-safe wrappers, append to default options
4. Run `go get` + `go mod tidy` to add dependencies
5. Temporarily edit `go.mod` to `go 1.24.7`, build with `GOTOOLCHAIN=local`
6. Build the binary, revert go.mod
7. Copy binary back, test, commit and push

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Vertex WithGoogleAuth panics | defer recover() wrapper |
| Generated handler files would be overwritten by codegen | Before hook + package-level var, zero handler changes |
| Binary size increase (~10-20 MB from cloud SDK deps) | Acceptable for a fork |
| Only messages/completions endpoints work on Vertex/Bedrock | Document limitation |
| go.mod requires Go 1.25 | Temporary edit + GOTOOLCHAIN=local |
| Bedrock EventStream decoder needs init() | bedrock package imported in cmdutil.go ensures it runs |
