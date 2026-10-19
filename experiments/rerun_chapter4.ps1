# Rerun every chapter-4 result with the current code (2026-10-11). Old results stay untouched.
#   powershell -ExecutionPolicy Bypass -File experiments\rerun_chapter4.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = '.venv\Scripts\python.exe'
$out = 'results\rerun_2026-10-11'
New-Item -ItemType Directory -Force $out | Out-Null

function Step($name, [scriptblock]$cmd) {
    $t = Get-Date
    "=== $name started $(Get-Date -Format HH:mm:ss)" | Tee-Object -Append "$out\progress.log"
    & $cmd *> "$out\$name.log"
    "=== $name finished (exit $LASTEXITCODE) in $([int]((Get-Date) - $t).TotalMinutes) min" | Tee-Object -Append "$out\progress.log"
}

Step 'synthetic-ablation' { & $py -m engine run experiments\synthetic-ablation.json --out "$out\synthetic-ablation" }
Step 'ability_check'      { & $py -m engine run experiments\ability_check.json --out "$out\ability_check" }
Step 'w_single_mechanism' { & $py experiments\w_single_mechanism.py --out "$out\w_single_mechanism" --seeds 0 1 2 }
Step 'w_recovery_v2'      { & $py experiments\w_recovery.py --out "$out\w_recovery_v2" --seeds 0 1 2 --students 120 }
Step 'w_power_regfix'     { & $py experiments\w_power.py --out "$out\w_power_regfix" --students 120 --seeds 0 1 2 3 4 }
Step 'w_power_regfix_placebo' { & $py experiments\w_power.py --out "$out\w_power_regfix" --students 120 --seeds 0 1 2 3 4 --mechanism none }
Step 'w_power_ability'    { & $py experiments\w_power.py --out "$out\w_power_ability" --students 120 --seeds 0 1 2 3 4 --ability }
Step 'w_power_ability_placebo' { & $py experiments\w_power.py --out "$out\w_power_ability" --students 120 --seeds 0 1 2 3 4 --ability --mechanism none }
Step 'edge_ci_k0.9'       { & $py experiments\edge_ci.py --out "$out\edge_ci" --students 120 --seeds 0 1 2 --boot 40 --workers 12 }
Step 'edge_ci_k0.6'       { & $py experiments\edge_ci.py --out "$out\edge_ci" --students 120 --seeds 0 1 2 --boot 40 --workers 12 --strength 0.6 --mechanisms both }
Step 'edge_ci_k0.3'       { & $py experiments\edge_ci.py --out "$out\edge_ci" --students 120 --seeds 0 1 2 --boot 40 --workers 12 --strength 0.3 --mechanisms both }
Step 'graph_value'        { & $py experiments\graph_value.py --out "$out\graph_value" --predictions results\rehearsal\predict\predictions.csv --events exports\sim-ds-120\events.csv }
Step 'rq4_faithfulness'   { & $py experiments\rq4_faithfulness.py --out "$out\rq4_faithfulness" --seeds 0 1 2 }
"=== ALL DONE $(Get-Date -Format HH:mm:ss)" | Tee-Object -Append "$out\progress.log"
