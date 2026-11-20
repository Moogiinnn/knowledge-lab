# ASSISTments 2009 comparison (chapter 4.1). Prepare first: python experiments/assist2009_prepare.py data/external/assist2009
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = '.venv\Scripts\python.exe'
& $py -W ignore -m engine run experiments\assist2009-none.json --out results\assist2009-none *> results\assist2009-none.log
& $py -W ignore -m engine run experiments\assist2009-data.json --out results\assist2009-data *> results\assist2009-data.log
"done" | Out-File results\assist2009.done
