#!/usr/bin/env node
// Hook PreToolUse: bloquea `git commit` cuando la rama actual es main.
// Escape explícito: incluir ALLOW_MAIN_COMMIT=1 en el comando.
const { execSync } = require('child_process');

let raw = '';
process.stdin.on('data', (c) => (raw += c));
process.stdin.on('end', () => {
  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    return; // sin input parseable, no bloquear
  }
  const cmd = ((input.tool_input && input.tool_input.command) || '').trim();

  // ¿Es un git commit? (inicio de comando o tras ; && | — no dentro de otras palabras)
  const esCommit = /(^|[;&|]\s*)git\s+(-[cC]\s+\S+\s+)*commit\b/.test(cmd);
  if (!esCommit) return;
  if (cmd.includes('ALLOW_MAIN_COMMIT=1')) return;

  let branch = '';
  try {
    branch = execSync('git rev-parse --abbrev-ref HEAD', {
      cwd: input.cwd || process.cwd(),
      encoding: 'utf8',
    }).trim();
  } catch {
    return; // fuera de un repo git, no bloquear
  }

  if (branch === 'main') {
    console.log(
      JSON.stringify({
        hookSpecificOutput: {
          hookEventName: 'PreToolUse',
          permissionDecision: 'deny',
          permissionDecisionReason:
            'Commit directo a main bloqueado (regla git-workflow: main estable, trabajo en ramas tipo/descripcion). ' +
            'Crea una rama primero: git checkout -b tipo/descripcion. ' +
            'Escape consciente: prefija el comando con ALLOW_MAIN_COMMIT=1.',
        },
      }),
    );
  }
});
