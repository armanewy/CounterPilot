import { runMaturityJob } from "./counterpilot-maturity.mjs";

try {
  const result = await runMaturityJob();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`Counterpilot maturity job failed: ${error.message}\n`);
  process.exitCode = 1;
}
