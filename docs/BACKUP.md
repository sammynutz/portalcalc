# Source backup

Repository: https://github.com/sammynutz/portalcalc

The source snapshot dated 2026-10-13 is on `codex/source-backup-2026-10-13`.
It includes Python source, regression checks, build specifications, documentation,
and the catalog, capacity tables, presets and assets needed to run/build the app.
Packaged releases, caches, generated study folders, PDFs and local credentials
are excluded by `.gitignore`. Excluded files remain on the local computer.

To back up later changes from this folder (while on the backup branch):

```powershell
git status --short
git add -A
git diff --cached --stat
git commit -m "Back up latest PortalCalc source"
git push
```

Review the staged files before committing. A Codex follow-up named
`PortalCalc daily source backup` also checks this folder each day at 6:00 pm
Australia/Sydney time and pushes relevant changes to the backup branch.
Keep the computer on and Codex running for this local scheduled check.
It defers while the project is being actively edited or another branch is
checked out, and reports failures or required action. Routine successful
backups are quiet. The main branch is kept separate so a backup does not
update the deployed service.

To restore into a new folder:

```powershell
git clone --branch codex/source-backup-2026-10-13 https://github.com/sammynutz/portalcalc.git PortalCalc-restored
```
