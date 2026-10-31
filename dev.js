// Starts the FastAPI backend and the Vite dev server together (Ctrl+C stops both).
import { spawn } from "child_process";
const py = process.platform === "win32" ? "python" : "python3";
const procs = [
  spawn(py, ["server.py"], { cwd: "ml_backend", stdio: "inherit", env: { ...process.env, ALLOWED_ORIGINS: "http://localhost:5173" } }),
  spawn("npx", ["vite"], { stdio: "inherit", shell: true }),
];
const stop = () => { procs.forEach(p => p.kill()); process.exit(); };
process.on("SIGINT", stop); process.on("SIGTERM", stop);
