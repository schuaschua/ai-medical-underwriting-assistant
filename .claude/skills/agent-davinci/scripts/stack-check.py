#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Check the project's code, pipelines and infrastructure against the architecture answers Da Vinci agreed.

Reads the answer tables in the architecture folder (org config `architecture_folder`, default
docs/architecture): `architecture.md` and the per-cloud-provider files beside it. Each row is keyed by its
question part (`1b`, `3d`, `5a` ...); the DevOps table may also be keyed by its Item names. Rows numbered
by the older eighteen questions are mapped to their parts.

Checked against the project's files, for every part whose answer names something this script knows:

| Part | Check | Found in |
| --- | --- | --- |
| 1a, 1b | cloud platforms (on-premises only: none) | Terraform providers, Bicep, CloudFormation, CDK, cloud SDKs |
| 1d | regions (warning) | location and region literals in IaC and parameter files |
| 2d | tag keys (warning) | IaC files |
| 3a | mobile: none for a pure web app, else the named framework (React Native, Flutter, MAUI, Ionic, native) | manifests, pubspec, csproj, Xcode and Android projects |
| 3d | front end, back end, database | package.json, requirements, pyproject, Pipfile, csproj, pom, Gradle, go.mod, Gemfile, composer, Prisma, IaC |
| 4a | AI platform (Azure OpenAI, OpenAI, Anthropic, Bedrock, Vertex AI) | SDKs, IaC |
| 4b | analytics platform (Databricks, Synapse, Snowflake, BigQuery, Redshift, Fabric) | SDKs, IaC |
| 5a | CI (GitHub Actions, Jenkins, CircleCI, Azure Pipelines, Bitbucket, GitLab) | pipeline files |
| 5b | CD and GitOps (Argo CD, Flux, Tekton) | manifests |
| 5c | source host (GitHub, Azure Repos, Bitbucket, GitLab) | the git remote `origin` |
| 5d | IaC (Terraform, Bicep, ARM, CloudFormation, CDK, Pulumi) | IaC files |
| 6a | environment names (warning) | parameter files, environment folders, pipeline `environment:` |
| 6c | each agreed scan (SAST, DAST, dependency, container image, IaC, secrets) runs in a pipeline; no stored cloud keys when federated identity was agreed | pipeline and scanner config files |

A tool the answers don't name, in a checked category, is `off_stack`, and fails the check. A named tool
with no files yet is `missing` (normal early on; fails only with --strict), except an agreed scan while
pipelines exist, which fails. Regions, tags and environments are `warnings`: literals there are often
legitimate. Anything answered that files can't show (the service model, policies, pillar ranking,
security level, naming, style, migration, push or pull, branching, gates and release strategy ...) is
listed under `judge`, with its answer, for the reviewer to hold the work against. Answers are matched by
name, so a tool written in an answer counts as agreed even as "not Bicep".

Files are the tracked and untracked-but-not-ignored ones in a git repository, else a walk; dependency and
tool folders (node_modules, .terraform, vendor, _bmad ...) are skipped either way.

Usage:
    uv run stack-check.py <project-root> [--strict]

Exit codes: 0=compliant (or no answers yet), 1=off-stack or a missing agreed scan (with --strict, any
agreed tool with no files), 2=bad path
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spine as spine_mod  # noqa: E402

YAML = ("*.yml", "*.yaml")
TOPICS = {
    "1a": "Environment", "1b": "Cloud platforms", "1c": "Service model", "1d": "Regions and availability",
    "1e": "On-premises link", "2a": "Policies", "2b": "Well-Architected priorities", "2c": "Security level",
    "2d": "Naming and tags", "3a": "Application type", "3b": "Architecture style", "3c": "Migration",
    "3d": "Approved stack", "4a": "AI", "4b": "Analytics", "5a": "CI", "5b": "CD and GitOps",
    "5c": "Source control and branching", "5d": "Infrastructure as code", "6a": "Environments",
    "6b": "Approval gates and release strategy", "6c": "Secrets and pipeline security",
}
# parts whose whole answer the file checks cover; every other answered part is listed under `judge`
FULLY_CHECKED = {"1b", "3a", "3d", "5a", "5d"}
LEGACY = {"1": "1a", "2": "1b", "3": "1c", "4": "2b", "5": "2a", "6": "2d", "7": "2d", "8": "1d", "9": "1e",
          "10": "4a", "11": "6a", "12": "3b", "13": "3c", "14": "2c", "15": "3d", "16": "3a", "17": "4b"}
DEVOPS_ITEMS = [(r"^ci\b|continuous integration", "5a"), (r"\bcd\b|gitops|deploy", "5b"), (r"source|branch", "5c"),
                (r"infrastructure|\biac\b", "5d"), (r"environment", "6a"), (r"gate|release|approv", "6b"),
                (r"secur|secret|scan", "6c")]

# A tool: names (regexes over the lower-cased answer), and any of: files (globs), content ((globs, regex)),
# deps (regexes over dependency names from the manifests), lang (a backend language), implies (tools a choice allows).
CATEGORIES: dict[str, dict] = {
    "cloud": {"part": "1b", "tools": {
        "azure": {"names": [r"\bazure\b"], "files": ["*.bicep", "*.bicepparam"],
                  "content": [(("*.tf", "*.tf.json"), r"\"?(hashicorp/)?azurerm\"?|provider\s+\"azurerm\"")],
                  "deps": [r"^azure-", r"^@azure/", r"^Azure\.", r"^Microsoft\.Azure\.", r"^com\.azure"]},
        "aws": {"names": [r"\baws\b", r"amazon web services"], "files": ["cdk.json"],
                "content": [(("*.tf", "*.tf.json"), r"hashicorp/aws\b|provider\s+\"aws\""), (YAML + ("*.json", "*.template"), r"AWSTemplateFormatVersion")],
                "deps": [r"^boto3$", r"^botocore$", r"^@aws-sdk/", r"^aws-sdk$", r"^AWSSDK\.", r"^software\.amazon\.awssdk", r"github\.com/aws/aws-sdk-go"]},
        "gcp": {"names": [r"\bgcp\b", r"google cloud"],
                "content": [(("*.tf", "*.tf.json"), r"hashicorp/google\b|provider\s+\"google\"")],
                "deps": [r"^google-cloud-", r"^@google-cloud/", r"^Google\.Cloud\.", r"^cloud\.google\.com/go"]},
    }},
    "mobile": {"part": "3a", "tools": {
        "react-native": {"names": [r"react native", r"\bexpo\b"], "deps": [r"^react-native$", r"^expo$"], "implies": ["react"]},
        "flutter": {"names": [r"flutter"], "content": [(("pubspec.yaml",), r"sdk:\s*flutter")]},
        "maui": {"names": [r"\bmaui\b"], "content": [(("*.csproj",), r"<UseMaui>\s*true")]},
        "ionic": {"names": [r"ionic", r"capacitor"], "deps": [r"^@capacitor/core$", r"^@ionic/"]},
        "native-ios": {"names": [r"(?<!react )\bnative\b", r"\bswift\b", r"objective-c"], "native": True,
                       "files": ["*.xcodeproj/project.pbxproj", "Podfile"]},
        "native-android": {"names": [r"(?<!react )\bnative\b", r"kotlin"], "native": True,
                           "files": ["AndroidManifest.xml"], "content": [(("build.gradle", "build.gradle.kts"), r"com\.android\.application")]},
    }},
    "frontend": {"part": "3d", "tools": {
        "react": {"names": [r"\breact\b(?! native)"], "deps": [r"^react$"]},
        "next": {"names": [r"next\.?js"], "deps": [r"^next$"], "implies": ["react"]},
        "angular": {"names": [r"angular"], "deps": [r"^@angular/core$"]},
        "vue": {"names": [r"\bvue"], "deps": [r"^vue$"]},
        "nuxt": {"names": [r"nuxt"], "deps": [r"^nuxt$"], "implies": ["vue"]},
        "svelte": {"names": [r"svelte"], "deps": [r"^svelte$", r"^@sveltejs/kit$"]},
        "blazor": {"names": [r"blazor"], "deps": [r"^Microsoft\.AspNetCore\.Components\.WebAssembly"]},
    }},
    "backend": {"part": "3d", "languages": {
        "dotnet": [r"\.net\b", r"\bc#", r"asp\.net"], "python": [r"python"], "node": [r"node", r"typescript", r"javascript"],
        "java": [r"\bjava\b", r"kotlin"], "go": [r"\bgo\b", r"golang"], "ruby": [r"ruby"], "php": [r"\bphp\b"]}, "tools": {
        "aspnet": {"names": [r"asp\.net"], "lang": "dotnet", "deps": [r"^sdk:Microsoft\.NET\.Sdk\.Web", r"^Microsoft\.NET\.Sdk\.Functions$", r"^Microsoft\.Azure\.Functions\.Worker$"]},
        "fastapi": {"names": [r"fastapi"], "lang": "python", "deps": [r"^fastapi$"]},
        "django": {"names": [r"django"], "lang": "python", "deps": [r"^django$"]},
        "flask": {"names": [r"flask"], "lang": "python", "deps": [r"^flask$"]},
        "express": {"names": [r"express"], "lang": "node", "deps": [r"^express$"]},
        "nestjs": {"names": [r"nest\.?js"], "lang": "node", "deps": [r"^@nestjs/core$"]},
        "fastify": {"names": [r"fastify"], "lang": "node", "deps": [r"^fastify$"]},
        "spring": {"names": [r"spring"], "lang": "java", "deps": [r"^spring-boot", r"^org\.springframework\.boot"]},
        "go": {"names": [r"\bgin\b", r"\becho\b", r"\bfiber\b"], "lang": "go", "files": ["go.mod"]},
        "rails": {"names": [r"rails"], "lang": "ruby", "deps": [r"^rails$"]},
        "laravel": {"names": [r"laravel"], "lang": "php", "deps": [r"^laravel/framework$"]},
    }},
    "database": {"part": "3d", "tools": {
        "postgres": {"names": [r"postgres"], "deps": [r"^psycopg", r"^asyncpg$", r"^pg$", r"^postgres$", r"^Npgsql", r"^postgresql$", r"github\.com/(lib/pq|jackc/pgx)"],
                     "content": [(("*.tf",), r"resource\s+\"azurerm_postgresql"), (("schema.prisma",), r"provider\s*=\s*\"postgresql\"")]},
        "mysql": {"names": [r"mysql", r"mariadb"], "deps": [r"^mysqlclient$", r"^pymysql$", r"^mysql2?$", r"^MySqlConnector$", r"^MySql\.Data", r"^mysql-connector", r"go-sql-driver/mysql"],
                  "content": [(("*.tf",), r"resource\s+\"azurerm_mysql"), (("schema.prisma",), r"provider\s*=\s*\"mysql\"")]},
        "sqlserver": {"names": [r"sql server", r"azure sql", r"\bmssql\b"], "deps": [r"^pymssql$", r"^mssql$", r"^tedious$", r"^(Microsoft|System)\.Data\.SqlClient$", r"EntityFrameworkCore\.SqlServer$", r"^mssql-jdbc$", r"go-mssqldb"],
                      "content": [(("*.tf",), r"resource\s+\"azurerm_(mssql|sql)_"), (("schema.prisma",), r"provider\s*=\s*\"sqlserver\"")]},
        "cosmos": {"names": [r"cosmos"], "deps": [r"^azure-cosmos$", r"^@azure/cosmos$", r"^Microsoft\.Azure\.Cosmos$", r"EntityFrameworkCore\.Cosmos$"],
                   "content": [(("*.tf",), r"resource\s+\"azurerm_cosmosdb")]},
        "mongodb": {"names": [r"mongo"], "deps": [r"^pymongo$", r"^motor$", r"^mongodb$", r"^mongoose$", r"^MongoDB\.Driver$", r"^mongodb-driver", r"mongo-driver"],
                    "content": [(("schema.prisma",), r"provider\s*=\s*\"mongodb\"")]},
        "dynamodb": {"names": [r"dynamo"], "deps": [r"^@aws-sdk/client-dynamodb$", r"^AWSSDK\.DynamoDBv2$"], "content": [(("*.tf",), r"resource\s+\"aws_dynamodb")]},
        "oracle": {"names": [r"oracle"], "deps": [r"^oracledb$", r"^cx_Oracle$", r"^Oracle\.ManagedDataAccess", r"^ojdbc"]},
        "firestore": {"names": [r"firestore"], "deps": [r"^google-cloud-firestore$", r"^@google-cloud/firestore$"]},
    }},
    "ai": {"part": "4a", "tools": {
        "azure-openai": {"names": [r"azure openai", r"azure ai"], "implies": ["openai"],
                         "deps": [r"^azure-ai-(openai|inference|projects)$", r"^@azure/openai$", r"^@azure-rest/ai-inference$", r"^Azure\.AI\.(OpenAI|Inference|Projects)$"],
                         "content": [(("*.tf",), r"kind\s*=\s*\"OpenAI\"")]},
        "openai": {"names": [r"(?<!azure )\bopenai\b"], "deps": [r"^openai$", r"^OpenAI$", r"^com\.openai", r"go-openai"]},
        "anthropic": {"names": [r"anthropic", r"claude"], "deps": [r"^anthropic$", r"^@anthropic-ai/sdk$", r"^Anthropic", r"anthropic-sdk-go"]},
        "bedrock": {"names": [r"bedrock"], "deps": [r"^@aws-sdk/client-bedrock", r"^AWSSDK\.Bedrock"], "content": [(("*.tf",), r"resource\s+\"aws_bedrock")]},
        "vertex": {"names": [r"vertex", r"gemini"], "deps": [r"^google-cloud-aiplatform$", r"^@google-cloud/vertexai$", r"^google-genai$", r"^google-generativeai$", r"^@google/(genai|generative-ai)$"]},
    }},
    "analytics": {"part": "4b", "tools": {
        "databricks": {"names": [r"databricks"], "deps": [r"^databricks-"], "content": [(("*.tf",), r"resource\s+\"(databricks_|azurerm_databricks)")]},
        "synapse": {"names": [r"synapse"], "content": [(("*.tf",), r"resource\s+\"azurerm_synapse")]},
        "snowflake": {"names": [r"snowflake"], "deps": [r"^snowflake-"], "content": [(("*.tf",), r"resource\s+\"snowflake_")]},
        "bigquery": {"names": [r"bigquery"], "deps": [r"^google-cloud-bigquery$", r"^@google-cloud/bigquery$"], "content": [(("*.tf",), r"resource\s+\"google_bigquery")]},
        "redshift": {"names": [r"redshift"], "deps": [r"^redshift[-_]connector$"], "content": [(("*.tf",), r"resource\s+\"aws_redshift")]},
        "fabric": {"names": [r"\bfabric\b"], "content": [(("*.tf",), r"resource\s+\"fabric_")]},
    }},
    "ci": {"part": "5a", "tools": {
        "github-actions": {"names": [r"github actions"], "files": [".github/workflows/*.yml", ".github/workflows/*.yaml"]},
        "jenkins": {"names": [r"jenkins"], "files": ["Jenkinsfile", "*.jenkinsfile", "Jenkinsfile.*"]},
        "circleci": {"names": [r"circle ?ci"], "files": [".circleci/config.yml", ".circleci/config.yaml"]},
        "azure-pipelines": {"names": [r"azure pipelines", r"(azure devops|ado)( \(ado\))? pipelines"],
                            "files": ["azure-pipelines.yml", "azure-pipelines.yaml", "*.azure-pipelines.yml", ".azuredevops/*.yml", ".azure-pipelines/*.yml"]},
        "bitbucket-pipelines": {"names": [r"bitbucket pipelines"], "files": ["bitbucket-pipelines.yml"]},
        "gitlab-ci": {"names": [r"gitlab ci", r"gitlab pipelines"], "files": [".gitlab-ci.yml"]},
    }},
    "cd": {"part": "5b", "tools": {
        "argocd": {"names": [r"argo ?cd"], "content": [(YAML, r"apiVersion:\s*argoproj\.io/")]},
        "flux": {"names": [r"\bflux(cd)?\b"], "content": [(YAML, r"apiVersion:\s*[a-z.]*toolkit\.fluxcd\.io/")]},
        "tekton": {"names": [r"tekton"], "content": [(YAML, r"apiVersion:\s*tekton\.dev/")]},
    }},
    "iac": {"part": "5d", "tools": {
        "terraform": {"names": [r"terraform", r"opentofu"], "files": ["*.tf", "*.tf.json"]},
        "bicep": {"names": [r"bicep"], "files": ["*.bicep", "*.bicepparam"]},
        "arm": {"names": [r"\barm\b", r"arm templates?"], "content": [(("*.json",), r"schema\.management\.azure\.com/schemas/[^\"]*deploymentTemplate\.json")]},
        "cloudformation": {"names": [r"cloudformation", r"\bsam\b"], "content": [(YAML + ("*.json", "*.template"), r"AWSTemplateFormatVersion")]},
        "cdk": {"names": [r"\b(aws )?cdk\b"], "files": ["cdk.json"]},
        "pulumi": {"names": [r"pulumi"], "files": ["Pulumi.yaml", "Pulumi.yml"]},
    }},
}
SCANS = {
    "sast": ([r"\bsast\b", r"static analysis"], r"codeql|semgrep|sonar|checkmarx|fortify|bandit|snyk code|security-code-scan|brakeman|gosec|spotbugs"),
    "dast": ([r"\bdast\b", r"dynamic analysis"], r"\bzap\b|zaproxy|stackhawk|burp|nuclei|invicti|netsparker"),
    "dependency": ([r"dependenc", r"\bsca\b", r"software composition"], r"dependabot|snyk|npm audit|yarn audit|pip-audit|safety check|dependency-check|dependency-review|osv-scanner|trivy fs|mend|whitesource|renovate|--vulnerable|govulncheck"),
    "container": ([r"container", r"\bimages?\b"], r"trivy|grype|docker scout|snyk container|twistlock|prisma cloud|anchore|clair|aquasec"),
    "iac": ([r"\biac\b", r"infrastructure"], r"checkov|tfsec|terrascan|kics|trivy config|cfn-nag|cfn-lint|tflint"),
    "secret": ([r"secret scan", r"secrets? detection", r"gitleaks", r"trufflehog"], r"gitleaks|trufflehog|detect-secrets|secretlint|ggshield"),
}
SCAN_CONFIG = [".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json", ".renovaterc*", "sonar-project.properties", ".snyk",
               ".gitleaks.toml", ".pre-commit-config.yaml", ".zap/*", ".trivyignore", ".checkov.y*ml", ".semgrep.y*ml"]
STORED_KEYS = r"AWS_SECRET_ACCESS_KEY|aws-secret-access-key|ARM_CLIENT_SECRET|AZURE_CLIENT_SECRET|AZURE_CREDENTIALS|client-secret|clientSecret|GOOGLE_CREDENTIALS|GCP_SA_KEY|credentials_json|servicePrincipalKey"
FEDERATED = r"oidc|federat|workload identity|no stored (cloud )?(keys|credentials|secrets)"
SOURCE_HOSTS = {"github": (r"github(?! actions)", r"github\.com"), "azure-repos": (r"azure repos|azure devops|\bado\b", r"dev\.azure\.com|visualstudio\.com"),
                "bitbucket": (r"bitbucket", r"bitbucket\.org"), "gitlab": (r"gitlab", r"gitlab\.")}
ENVS = {"dev": ["dev", "development"], "test": ["test", "testing"], "qa": ["qa"], "sit": ["sit"], "uat": ["uat"],
        "staging": ["staging", "stage", "stg"], "preprod": ["preprod", "preproduction"], "prod": ["prod", "production", "prd"],
        "sandbox": ["sandbox"], "dr": ["dr"], "perf": ["perf", "performance"]}
REGION_HINT = r"[a-z]{2}-[a-z]+-\d|\b(east|west|north|south|central)|europe|asia|australia|uae|uk\b|india|japan|canada|brazil"
SKIP_DIRS = {".git", "node_modules", ".terraform", "vendor", "third_party", ".venv", "venv", "__pycache__",
             "_bmad", "_bmad-output", ".claude", "dist", "build", "target", ".tox", "Pods", "bin", "obj"}
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")


# --- answers --------------------------------------------------------------------------------------------

def table_answers(text: str) -> dict[str, list[str]]:
    """Answer cells keyed by question part, from every table with an Answer column."""
    found: dict[str, list[str]] = {}
    header: list[str] | None = None
    for line in text.splitlines():
        row = TABLE_ROW.match(line)
        if not row:
            header = None
            continue
        cells = [c.strip() for c in row.group(1).split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells) or "answer" not in header:
            continue
        answer = cells[header.index("answer")] if header.index("answer") < len(cells) else ""
        key = cells[0].strip("* ").lower()
        part = key if re.fullmatch(r"[1-6][a-e]", key) else LEGACY.get(key) if key.isdigit() else None
        if part is None and "item" in header:
            item = cells[header.index("item")].lower()
            part = next((p for rx, p in DEVOPS_ITEMS if re.search(rx, item)), None)
        if part and answer and not set(answer) <= set("-: "):
            found.setdefault(part, []).append(answer)
    return found


def all_answers(arch: Path) -> dict[str, str]:
    parts: dict[str, list[str]] = {}
    docs = [arch] + [arch.parent / n for n in spine_mod.architecture_docs(arch) if n.endswith(".md")]
    for doc in docs:
        for part, answers in table_answers(doc.read_text(encoding="utf-8", errors="ignore")).items():
            parts.setdefault(part, []).extend(answers)
    return {p: " ; ".join(a) for p, a in sorted(parts.items())}


def norm(text: str) -> str:
    return text.lower().replace("-", " ")


# --- project files --------------------------------------------------------------------------------------

class Project:
    def __init__(self, root: Path):
        self.root = root
        self.files = self._files()
        self._text: dict[str, str] = {}
        self.deps = self._deps()

    def _files(self) -> list[str]:
        try:
            out = subprocess.run(["git", "-C", str(self.root), "ls-files", "-co", "--exclude-standard"],
                                 capture_output=True, text=True, check=True).stdout
            files = [f for f in out.splitlines() if f]
        except (OSError, subprocess.CalledProcessError):
            files = [p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()]
        return sorted(f for f in files if not SKIP_DIRS.intersection(f.split("/")[:-1]) and (self.root / f).is_file())

    def text(self, rel: str) -> str:
        if rel not in self._text:
            try:
                self._text[rel] = (self.root / rel).read_text(encoding="utf-8", errors="ignore")[:500_000]
            except OSError:
                self._text[rel] = ""
        return self._text[rel]

    def glob(self, globs) -> list[str]:
        out = []
        for f in self.files:
            name = f.rsplit("/", 1)[-1]
            if any(fnmatch.fnmatchcase(f, g) or ("/" not in g and fnmatch.fnmatchcase(name, g))
                   or ("/" in g and fnmatch.fnmatchcase(f, "*/" + g)) for g in globs):
                out.append(f)
        return out

    def grep(self, globs, pattern: str) -> list[str]:
        rx = re.compile(pattern)
        return [f for f in self.glob(globs) if rx.search(self.text(f))]

    def _deps(self) -> dict[str, set[str]]:
        """Dependency name -> manifest files that declare it."""
        deps: dict[str, set[str]] = {}

        def add(name: str, f: str) -> None:
            name = name.strip()
            if name:
                deps.setdefault(name, set()).add(f)

        for f in self.glob(["package.json", "composer.json"]):
            try:
                data = json.loads(self.text(f))
            except json.JSONDecodeError:
                continue
            for key in ("dependencies", "devDependencies", "peerDependencies", "require", "require-dev"):
                for name in (data.get(key) or {}):
                    add(name, f)
        for f in self.glob(["requirements*.txt", "requirements/*.txt"]):
            for line in self.text(f).splitlines():
                m = re.match(r"^\s*([A-Za-z0-9_.\-]+)", line)
                if m and not line.lstrip().startswith(("#", "-")):
                    add(m.group(1).lower().replace("_", "-"), f)
        for f in self.glob(["pyproject.toml", "Pipfile"]):
            try:
                data = tomllib.loads(self.text(f))
            except tomllib.TOMLDecodeError:
                continue
            project = data.get("project", {})
            specs = list(project.get("dependencies", []))
            for group in list(project.get("optional-dependencies", {}).values()) + list(data.get("dependency-groups", {}).values()):
                specs += [s for s in group if isinstance(s, str)]
            names = [re.match(r"^\s*([A-Za-z0-9_.\-]+)", s).group(1) for s in specs if re.match(r"^\s*[A-Za-z0-9]", s)]
            poetry = data.get("tool", {}).get("poetry", {})
            names += list(poetry.get("dependencies", {})) + list(data.get("packages", {})) + list(data.get("dev-packages", {}))
            for group in poetry.get("group", {}).values():
                names += list(group.get("dependencies", {}))
            for name in names:
                add(name.lower().replace("_", "-"), f)
        for f in self.glob(["*.csproj", "*.fsproj", "Directory.Packages.props"]):
            text = self.text(f)
            for m in re.finditer(r"<Package(?:Reference|Version)\s+[^>]*Include=\"([^\"]+)\"", text):
                add(m.group(1), f)
            for m in re.finditer(r"<Project\s+Sdk=\"([^\"]+)\"", text):
                add("sdk:" + m.group(1), f)
        for f in self.glob(["pom.xml"]):
            for m in re.finditer(r"<(?:artifactId|groupId)>([^<]+)</", self.text(f)):
                add(m.group(1), f)
        for f in self.glob(["build.gradle", "build.gradle.kts"]):
            for m in re.finditer(r"['\"]([\w.\-]+):([\w.\-]+)(?::[^'\"]*)?['\"]", self.text(f)):
                add(m.group(1), f)
                add(m.group(2), f)
            for m in re.finditer(r"id\s*\(?\s*['\"]([\w.\-]+)['\"]", self.text(f)):
                add(m.group(1), f)
        for f in self.glob(["go.mod"]):
            for m in re.finditer(r"^\s*(?:require\s+)?([\w.\-]+\.[a-z]+/[\w.\-/]+)\s+v", self.text(f), re.M):
                add(m.group(1), f)
        for f in self.glob(["Gemfile"]):
            for m in re.finditer(r"^\s*gem\s+['\"]([\w\-]+)['\"]", self.text(f), re.M):
                add(m.group(1), f)
        return deps

    def dep_files(self, patterns) -> list[str]:
        out: set[str] = set()
        for pattern in patterns:
            rx = re.compile(pattern)
            for name, files in self.deps.items():
                if rx.search(name):
                    out |= files
        return sorted(out)


def tool_files(project: Project, spec: dict) -> list[str]:
    hits = set(project.glob(spec.get("files", [])))
    for globs, pattern in spec.get("content", []):
        hits |= set(project.grep(globs, pattern))
    hits |= set(project.dep_files(spec.get("deps", [])))
    return sorted(hits)


# --- checks ---------------------------------------------------------------------------------------------

def names(spec: dict, text: str) -> bool:
    return any(re.search(n, text) for n in spec["names"])


def check_category(key: str, cat: dict, answers: dict[str, str], project: Project, chosen_all: set[str]) -> dict | None:
    part = cat["part"]
    text = norm(answers.get(part, ""))
    tools = cat["tools"]
    chosen = {t for t, spec in tools.items() if names(spec, text)}
    langs = {lang for lang, rxs in cat.get("languages", {}).items() if any(re.search(r, text) for r in rxs)}
    forbid_all = False
    if key == "cloud" and not chosen:
        env = norm(answers.get("1a", ""))
        forbid_all = bool(re.search(r"on ?prem", env)) and not re.search(r"hybrid|cloud", env)
        part = "1a" if forbid_all else part
    if key == "mobile" and not chosen and text:
        forbid_all = bool(re.search(r"\bweb\b", text)) and not re.search(r"mobile|hybrid|ios|android|app store", text)
    if not chosen and not langs and not forbid_all:
        return None
    found = {t: fs for t, spec in tools.items() if (fs := tool_files(project, spec))}
    if key == "mobile" and any(t in found for t, s in tools.items() if not s.get("native")):
        found = {t: fs for t, fs in found.items() if not tools[t].get("native")}

    def allowed(tool: str) -> bool:
        if tool in chosen or tool in chosen_all:
            return True
        lang = tools[tool].get("lang")
        return bool(lang and lang in langs and not any(tools[c].get("lang") == lang for c in chosen))

    return {
        "check": key, "part": part, "topic": TOPICS[part], "answer": answers.get(part, ""),
        "agreed": sorted(chosen | {f"language:{lang}" for lang in langs}),
        "found": {t: fs for t, fs in found.items() if allowed(t)},
        "off_stack": [{"tool": t, "files": fs} for t, fs in found.items() if not allowed(t)],
        "missing": [t for t in sorted(chosen) if t not in found],
    }


def check_scans(answers: dict[str, str], project: Project, ci_files: list[str]) -> dict | None:
    text = norm(answers.get("6c", ""))
    wanted = [s for s, (rxs, _) in SCANS.items() if any(re.search(r, text) for r in rxs)]
    federated = bool(re.search(FEDERATED, text))
    if not wanted and not federated:
        return None
    files = sorted(set(ci_files) | set(project.glob(SCAN_CONFIG)))
    blob = "\n".join(project.text(f) for f in files).lower()
    result = {"check": "scans", "part": "6c", "topic": TOPICS["6c"], "answer": answers.get("6c", ""),
              "agreed": wanted + (["federated identity"] if federated else []), "pipelines": bool(ci_files),
              "found": {s: True for s in wanted if re.search(SCANS[s][1], blob)},
              "missing": [s for s in wanted if not re.search(SCANS[s][1], blob)], "off_stack": []}
    if federated:
        keyed = [f for f in ci_files if re.search(STORED_KEYS, project.text(f))]
        if keyed:
            result["off_stack"].append({"tool": "stored cloud credentials", "files": keyed})
    return result


def check_source(answers: dict[str, str], root: Path) -> dict | None:
    text = norm(answers.get("5c", ""))
    chosen = [h for h, (rx, _) in SOURCE_HOSTS.items() if re.search(rx, text)]
    if not chosen:
        return None
    try:
        remote = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    except OSError:
        remote = ""
    host = next((h for h, (_, rx) in SOURCE_HOSTS.items() if remote and re.search(rx, remote)), None)
    return {"check": "source_host", "part": "5c", "topic": TOPICS["5c"], "answer": answers.get("5c", ""), "agreed": chosen,
            "found": {host: [remote]} if host in chosen else {},
            "off_stack": [{"tool": host, "files": [remote]}] if host and host not in chosen else [],
            "missing": chosen if not remote else []}


def iac_files(project: Project) -> list[str]:
    return sorted(set(tool_files(project, CATEGORIES["iac"]["tools"]["terraform"]) + tool_files(project, CATEGORIES["iac"]["tools"]["bicep"])
                      + project.glob(["*.tfvars", "*.parameters.json"])
                      + tool_files(project, CATEGORIES["iac"]["tools"]["cloudformation"])
                      + tool_files(project, CATEGORIES["iac"]["tools"]["arm"])))


def warn_regions(answers: dict[str, str], project: Project, iac: list[str]) -> list[dict]:
    text = answers.get("1d", "").lower()
    if not re.search(REGION_HINT, text):
        return []
    flat = re.sub(r"[^a-z0-9]", "", text)
    rx = re.compile(r"\b(?:location|region|primary_location|secondary_location|default_region)\s*[:=]\s*['\"]([A-Za-z0-9 \-]+)['\"]")
    seen: dict[str, list[str]] = {}
    for f in iac:
        for m in rx.finditer(project.text(f)):
            value = m.group(1)
            key = re.sub(r"[^a-z0-9]", "", value.lower())
            if key and key != "global" and key not in flat:
                seen.setdefault(value, []).append(f)
    return [{"check": "regions", "part": "1d", "value": v, "files": sorted(set(fs)),
             "message": f"region '{v}' is not among the agreed regions"} for v, fs in seen.items()]


def warn_tags(answers: dict[str, str], project: Project, iac: list[str]) -> list[dict]:
    m = re.search(r"tags?\b[^:;]*[:\-]\s*([^;]+)", answers.get("2d", ""), re.I)
    if not m or not iac:
        return []
    keys = [k.strip(" .`()") for k in re.split(r",|/|\band\b", m.group(1)) if k.strip(" .`()")]
    blob = re.sub(r"[^a-z0-9]", "", "\n".join(project.text(f) for f in iac).lower())
    out = []
    for key in keys:
        flat = re.sub(r"[^a-z0-9]", "", key.lower())
        variants = {flat, flat.replace("centre", "center"), flat.replace("center", "centre")}
        if flat and len(key.split()) <= 3 and not any(v in blob for v in variants):
            out.append({"check": "tags", "part": "2d", "value": key, "files": [],
                        "message": f"tag '{key}' appears in no infrastructure file"})
    return out


def env_tokens(text: str) -> set[str]:
    words = set(re.split(r"[^a-z0-9]+", text.lower()))
    return {canon for canon, syn in ENVS.items() if words & set(syn)}


def warn_environments(answers: dict[str, str], project: Project, ci_files: list[str]) -> list[dict]:
    agreed = env_tokens(answers.get("6a", ""))
    if not agreed:
        return []
    found: dict[str, set[str]] = {}
    for f in project.glob(["*.tfvars", "*.bicepparam", "*.parameters.json"]):
        for canon in env_tokens(f.rsplit("/", 1)[-1].split(".")[0]):
            found.setdefault(canon, set()).add(f)
    for f in project.files:
        parts = f.split("/")
        for i, p in enumerate(parts[:-1]):
            if p in ("env", "envs", "environments", "overlays") and i + 1 < len(parts) - 1:
                for canon in env_tokens(parts[i + 1]):
                    found.setdefault(canon, set()).add(f)
    for f in ci_files:
        for m in re.finditer(r"environment:\s*(?:name:\s*)?['\"]?([\w\-]+)", project.text(f)):
            for canon in env_tokens(m.group(1)):
                found.setdefault(canon, set()).add(f)
    return [{"check": "environments", "part": "6a", "value": canon, "files": sorted(fs)[:10],
             "message": f"environment '{canon}' is not among the agreed environments"}
            for canon, fs in sorted(found.items()) if canon not in agreed]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_root")
    ap.add_argument("--strict", action="store_true", help="also fail when an agreed tool has no files yet")
    args = ap.parse_args(argv)
    root = Path(args.project_root).resolve()
    if not root.is_dir():
        print(json.dumps({"status": "error", "message": f"not a folder: {root}"}))
        return 2

    arch = spine_mod.architecture_file(root)
    result: dict = {"architecture": arch.as_posix()}
    if not arch.is_file():
        print(json.dumps({**result, "status": "no_architecture",
                          "message": "No architecture.md: nothing agreed to check against yet (Da Vinci's principles, capability AP)."}, indent=2))
        return 0
    answers = all_answers(arch)
    if not answers:
        print(json.dumps({**result, "status": "no_answers",
                          "message": "architecture.md has no answer tables keyed by question part yet: Da Vinci's principles round writes them."}, indent=2))
        return 0

    project = Project(root)
    chosen_all: set[str] = set()
    for cat in CATEGORIES.values():
        text = norm(answers.get(cat["part"], ""))
        for spec in cat["tools"].values():
            if names(spec, text):
                chosen_all |= set(spec.get("implies", []))
    checks = [c for key, cat in CATEGORIES.items() if (c := check_category(key, cat, answers, project, chosen_all))]
    ci_files = sorted({f for t in CATEGORIES["ci"]["tools"].values() for f in tool_files(project, t)})
    checks += [c for c in (check_scans(answers, project, ci_files), check_source(answers, root)) if c]
    iac = iac_files(project)
    warnings = warn_regions(answers, project, iac) + warn_tags(answers, project, iac) + warn_environments(answers, project, ci_files)

    off_stack = [{"check": c["check"], "part": c["part"], "agreed": c["agreed"], **o} for c in checks for o in c["off_stack"]]
    missing = [{"check": c["check"], "part": c["part"], "tool": t} for c in checks for t in c["missing"]]
    failing_missing = [m for m in missing if args.strict or (m["check"] == "scans" and next(c for c in checks if c["check"] == "scans")["pipelines"])]
    checked_parts = {c["part"] for c in checks}
    result.update({
        "status": "off_stack" if off_stack else ("missing" if failing_missing else "compliant"),
        "off_stack": off_stack,
        "missing": missing,
        "warnings": warnings,
        "checks": checks,
        "judge": [{"part": p, "topic": TOPICS.get(p, p), "answer": a} for p, a in answers.items()
                  if p not in FULLY_CHECKED or p not in checked_parts],
    })
    print(json.dumps(result, indent=2))
    return 1 if off_stack or failing_missing else 0


if __name__ == "__main__":
    sys.exit(main())
