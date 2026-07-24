"""One-off test: confirm a modal.Sandbox with outbound_domain_allowlist
actually blocks everything except what's allowlisted, before trusting it
with the real Optimizer run.
"""

import modal

app = modal.App.lookup("secret-loyalties-grader", create_if_missing=False)
image = modal.Image.debian_slim().apt_install("curl")

GRADER_HOST = "moh-murr--secret-loyalties-grader-web.modal.run"

sb = modal.Sandbox.create(
    app=app,
    image=image,
    outbound_domain_allowlist=[GRADER_HOST, "openrouter.ai"],
    timeout=60,
)

print(f"sandbox created: {sb.object_id}")

print("\n--- curl to allowlisted grader endpoint (should succeed) ---")
p = sb.exec("curl", "-s", "-m", "10", f"https://{GRADER_HOST}/health")
print("stdout:", p.stdout.read())
print("stderr:", p.stderr.read())
print("returncode:", p.wait())

print("\n--- curl to openrouter.ai (should succeed, at least connect) ---")
p = sb.exec("curl", "-s", "-m", "10", "-o", "/dev/null", "-w", "%{http_code}", "https://openrouter.ai/api/v1/models")
print("http_code:", p.stdout.read())
print("returncode:", p.wait())

print("\n--- curl to a NON-allowlisted domain (should FAIL/block) ---")
p = sb.exec("curl", "-s", "-m", "10", "-o", "/dev/null", "-w", "http_code=%{http_code} exit=%{exitcode}", "https://example.com")
print("stdout:", p.stdout.read())
print("stderr:", p.stderr.read())
print("returncode:", p.wait())

sb.terminate()
print("\nsandbox terminated")
