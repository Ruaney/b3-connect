$ErrorActionPreference = 'Stop'
$sources = (Get-Content -LiteralPath (Join-Path $PSScriptRoot '../catalog/sources.json') -Raw | ConvertFrom-Json).sources
$flows = (Get-Content -LiteralPath (Join-Path $PSScriptRoot '../catalog/flows.json') -Raw | ConvertFrom-Json).flows
foreach ($items in @(@{ name = 'fontes'; values = @($sources) }, @{ name = 'fluxos'; values = @($flows) })) {
    if (@($items.values | Group-Object id | Where-Object Count -gt 1).Count) { throw "IDs duplicados em $($items.name)" }
}
foreach ($source in $sources) {
    if (-not $source.id -or -not $source.title -or -not $source.limitation) { throw 'Fonte incompleta' }
    $uri = [uri]$source.url
    if ($uri.Scheme -ne 'https' -or -not ($uri.Host -eq 'b3.com.br' -or $uri.Host.EndsWith('.b3.com.br'))) { throw "Fonte não oficial: $($source.id)" }
}
foreach ($flow in $flows) {
    if (-not $flow.id -or -not $flow.name -or -not $flow.pending -or @($flow.sources).Count -eq 0) { throw 'Fluxo incompleto' }
    foreach ($sourceId in $flow.sources) {
        if ($sourceId -notin $sources.id) { throw "Fonte inexistente: $sourceId" }
    }
    foreach ($state in @('mapped', 'documented', 'implemented', 'tested_locally', 'validated_b3')) {
        if ($flow.$state -isnot [bool]) { throw "Estado inválido: $($flow.id)/$state" }
        if ($state -ne 'mapped' -and $flow.$state -and -not $flow.evidence.$state) { throw "Falta evidência: $($flow.id)/$state" }
    }
}
Write-Output "Integridade verificada: $(@($sources).Count) fontes; $(@($flows).Count) fluxos."
