@echo off
setlocal

cd /d "%~dp0"

set "REMOTE=origin"
set "COMMIT_MSG=%*"
if "%COMMIT_MSG%"=="" set "COMMIT_MSG=sync project updates"

git --version >nul 2>nul
if errorlevel 1 (
  echo Git is not installed or not available in PATH.
  pause
  exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
  echo This folder is not a Git repository.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%b in (`git branch --show-current`) do set "BRANCH=%%b"
if "%BRANCH%"=="" set "BRANCH=main"

git remote get-url %REMOTE% >nul 2>nul
if errorlevel 1 (
  echo Missing Git remote: %REMOTE%
  pause
  exit /b 1
)

echo Repository:
git remote get-url %REMOTE%
echo.
echo Branch: %BRANCH%
echo.

echo Staging changes...
git add -A
if errorlevel 1 (
  echo Failed to stage changes.
  pause
  exit /b 1
)

git diff --cached --quiet
if errorlevel 1 (
  echo Committing changes...
  git commit -m "%COMMIT_MSG%"
  if errorlevel 1 (
    echo Commit failed.
    pause
    exit /b 1
  )
) else (
  echo No local changes to commit.
)

echo.
echo Pushing to GitHub...
git push -u %REMOTE% %BRANCH%
if errorlevel 1 (
  echo Push failed. Check your GitHub login, network, or branch permissions.
  pause
  exit /b 1
)

echo.
echo Synced successfully.
pause
