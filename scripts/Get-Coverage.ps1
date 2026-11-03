$ErrorActionPreference = 'Stop'
$catalog = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../catalog/flows.json') -Raw | ConvertFrom-Json
$flows = @($catalog.flows)
Write-Output "Inventário $($catalog.version): $($flows.Count) fluxos conhecidos; descoberta incompleta."
foreach ($state in @('mapped', 'documented', 'implemented', 'tested_locally', 'validated_b3')) {
    $count = @($flows | Where-Object { $_.$state -eq $true }).Count
    $percent = if ($flows.Count) { [math]::Round(100 * $count / $flows.Count, 1) } else { 0 }
    Write-Output "${state}: $count/$($flows.Count) ($percent%)"
}
Write-Output 'Percentuais relativos ao inventário, não à totalidade da B3.'
