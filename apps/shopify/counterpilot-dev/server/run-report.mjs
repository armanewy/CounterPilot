import { runReportJob } from "./counterpilot-report.mjs";

try {
  const result = await runReportJob();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`Counterpilot report job failed: ${error.message}\n`);
  process.exitCode = 1;
}
