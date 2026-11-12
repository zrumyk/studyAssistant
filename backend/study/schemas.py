from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrictModel):
    document_id: str
    locator: str
    excerpt: str


class ConflictEvidence(StrictModel):
    conflict_index: int = Field(ge=0)
    source_a: Source
    source_b: Source


class ConflictPair(StrictModel):
    source_a: Source
    source_b: Source


class Step(StrictModel):
    title: str
    instruction: str
    kind: Literal["code", "write", "manual", "report", "diagram", "fetch"]
    language: Literal[
        "python",
        "javascript",
        "typescript",
        "c",
        "cpp",
        "java",
        "csharp",
        "r",
        "sql",
        "octave",
    ] = "python"
    expected: str
    timeout_seconds: int = Field(default=600, ge=1, le=7200)
    dependencies: list[str] = Field(default_factory=list, max_length=30)
    urls: list[str] = Field(default_factory=list, max_length=10)
    max_download_bytes: int = Field(default=50_000_000, ge=1, le=3_000_000_000)
    artifact_inputs: list[str] = Field(default_factory=list, max_length=100,
                                     description='Canonical persistent relative FILE paths from earlier declared outputs or existing inputs/; no directories or current code entrypoint')
    artifact_outputs: list[str] = Field(default_factory=list, max_length=100,
                                      description='Canonical persistent relative FILE paths published by this code/write/fetch step; no directories, /tmp or private capture')

    @field_validator('language',mode='before')
    @classmethod
    def unused_language(cls,value,info):
        if value is None and info.data.get('kind') in ('manual','fetch','write','report','diagram'):
            return 'python'
        return value

    @field_validator('max_download_bytes',mode='before')
    @classmethod
    def unused_download_limit(cls,value,info):
        if value in (None,0) and info.data.get('kind') in ('code','manual','write','report','diagram'):
            return 50_000_000
        return value


class PlanDraft(StrictModel):
    artifact_contract_version: Literal[0, 1] = 1
    goal: str
    requirements: list[str]
    missing: list[str]
    conflicts: list[str]
    warnings: list[str] = Field(default_factory=list,max_length=40)
    report_sections: list[str]
    steps: list[Step] = Field(min_length=1, max_length=40)


class Plan(PlanDraft):
    # Existing stored plans must not silently acquire a new execution contract.
    artifact_contract_version: Literal[0, 1] = 0
    sources: list[Source] = Field(default_factory=list)
    conflict_evidence: list[ConflictEvidence] = Field(default_factory=list,max_length=40)


class FileWrite(StrictModel):
    path: str
    content: str


class Action(StrictModel):
    files: list[FileWrite] = Field(default_factory=list, max_length=30)
    entrypoint: str = ""
    expected_files: list[str] = Field(default_factory=list)
    checks: list[FileWrite] = Field(default_factory=list, max_length=10)
    explanation: str
    manual_instructions: str = ""
    requires_user: bool = False
    url: str = ""
    stdin_text: str = Field(default='', max_length=65536,
                           description='Finite UTF-8 stdin for this run; empty string means immediate EOF')

    @field_validator('stdin_text')
    @classmethod
    def bounded_stdin(cls, value):
        from .execution_protocol import stdin_bytes
        stdin_bytes(value)
        return value


class PythonAction(Action):
    entrypoint: Literal["main.py"] = "main.py"
    files: list[FileWrite] = Field(min_length=1, max_length=30)
    checks: list[FileWrite] = Field(min_length=1, max_length=10)


class RepairAdvice(StrictModel):
    cause: str = Field(max_length=1000)
    changes: list[str] = Field(max_length=5)
    preserve: list[str] = Field(max_length=5)


class SourceEdit(StrictModel):
    path: str
    before: str = Field(min_length=1,max_length=12000)
    after: str = Field(max_length=16000)


class PythonRepair(StrictModel):
    edits: list[SourceEdit] = Field(min_length=1,max_length=16)
    replace_expected_files: bool = False
    expected_files: list[str] = Field(default_factory=list)
    explanation: str = Field(max_length=1500)


class PythonProgram(StrictModel):
    main_code: str = Field(min_length=1,max_length=60000,description='Pure Python source for main.py for this step only, not JSON or a file manifest')
    checker_code: str = Field(min_length=1,max_length=30000,description='Independent executable Python checker source with invoked nontrivial asserts; do not retrain models')
    supporting_files: list[FileWrite] = Field(default_factory=list, max_length=28,
        description='Additional model-authored source/text files for this step, with exact relative paths; include declared source deliverables here, never overwrite imported/consumed inputs')
    expected_files: list[str] = Field(default_factory=list,max_length=100,description='Produced data/images/models, never implementation or checker source files')
    explanation: str
    stdin_text: str = Field(default='', max_length=65536)

    @field_validator('supporting_files')
    @classmethod
    def unique_supporting_files(cls, files):
        from .artifact_contracts import artifact_path
        seen = {'main.py', 'check_current_step.py'}
        for file in files:
            name = artifact_path(file.path)
            if name.casefold() in seen or name.split('/')[0].casefold() == 'inputs':
                raise ValueError('Supporting files require unique paths and cannot replace main/check/inputs')
            seen.add(name.casefold())
        return files

    def as_action(self):
        return PythonAction(files=[FileWrite(path='main.py',content=self.main_code), *self.supporting_files],
                            checks=[FileWrite(path='check_current_step.py',content=self.checker_code)],
                            expected_files=self.expected_files,explanation=self.explanation,
                            stdin_text=self.stdin_text)


class ActionCheckpoint(StrictModel):
    model: str
    revision: int
    action: Action
    written_to_workspace: bool = False
    generation_contract: str = ''


class Assessment(StrictModel):
    satisfied: bool
    explanation: str
    needs_user: bool = False


class ContentReview(StrictModel):
    blocking_issues: list[str] = Field(max_length=8)
    notes: list[str] = Field(default_factory=list, max_length=8)
    explanation: str = Field(max_length=2000)

    @field_validator('blocking_issues', 'notes', mode='before')
    @classmethod
    def group_overflow_items(cls, value):
        # Preserve every decision/note when the provider exceeds the requested
        # list length. Cosmetic overflow must not discard a completed review.
        if isinstance(value, list) and len(value)>8 and all(isinstance(item,str) for item in value):
            return value[:7] + ['\n'.join('- '+item for item in value[7:])]
        return value

    @field_validator('explanation',mode='before')
    @classmethod
    def bound_explanation(cls,value):
        # Only shorten narrative. Never discard blocking decisions or notes.
        return value[:2000] if isinstance(value,str) else value

    @property
    def satisfied(self):
        return not self.blocking_issues


class ReportSection(StrictModel):
    heading: str
    text: str


class Report(StrictModel):
    title: str
    sections: list[ReportSection]


class ProjectCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=200)
    task: str = Field(min_length=1, max_length=30000)
    variant: str = Field(default="", max_length=200)


class ImportRequest(StrictModel):
    path: str
    project_id: str | None = None
    subject: str = ""
    role: Literal["task", "theory", "reference", "data", "result"] = "theory"
