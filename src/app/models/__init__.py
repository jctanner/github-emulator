from app.models.user import User
from app.models.token import PersonalAccessToken
from app.models.organization import Organization, OrgMembership, OrgRoleAssignment
from app.models.team import Team, TeamMembership, TeamRepo
from app.models.repository import Repository, Collaborator, StarredRepo
from app.models.branch import Branch, BranchProtection
from app.models.issue import Issue, IssueAssignee, IssueLabel
from app.models.pull_request import PullRequest
from app.models.auto_merge import PullRequestAutoMerge
from app.models.label import Label
from app.models.milestone import Milestone
from app.models.comment import IssueComment, PRReviewComment, CommitComment
from app.models.review import Review
from app.models.reaction import Reaction
from app.models.webhook import Webhook, WebhookDelivery
from app.models.release import Release, ReleaseAsset
from app.models.deploy_key import DeployKey
from app.models.commit_status import CommitStatus
from app.models.check import CheckRun, CheckSuite
from app.models.event import Event
from app.models.issue_event import IssueEvent
from app.models.notification import Notification
from app.models.actions import EnterpriseRunnerRegistrationToken, Runner, RunnerSession, RegistrationToken, Workflow, WorkflowRun, WorkflowJob, Secret, Variable, OrgVariable, OrgVariableRepository
from app.models.gist import Gist, GistFile
from app.models.ssh_key import SSHKey, GPGKey
from app.models.search_index import FileContent, CommitMetadata
from app.models.import_job import ImportJob
from app.models.apps import GitHubApp, AppInstallation, AppInstallationToken
from app.models.artifact import WorkflowArtifact

from app.database import Base

# GitHub never hands out an id twice. SQLite does unless a table is declared
# AUTOINCREMENT: an INTEGER PRIMARY KEY otherwise takes max(rowid) + 1, so
# deleting the newest rows (a repository and its runs, an issue, an artifact)
# frees their ids for the next insert. Every table is declared that way here,
# once, after all of them are registered, so a new model cannot miss it.
# SQLite applies it only to a single INTEGER primary key; the one table keyed
# by a string (gists) is unaffected. Migration 0016 rebuilds the tables of an
# existing database the same way.
for _table in Base.metadata.tables.values():
    _table.dialect_options["sqlite"]["autoincrement"] = True
del _table

__all__ = [
    "User",
    "PersonalAccessToken",
    "Organization", "OrgMembership", "OrgRoleAssignment",
    "Team", "TeamMembership", "TeamRepo",
    "Repository", "Collaborator", "StarredRepo",
    "Branch", "BranchProtection",
    "Issue", "IssueAssignee", "IssueLabel",
    "PullRequest",
    "PullRequestAutoMerge",
    "Label",
    "Milestone",
    "IssueComment", "PRReviewComment", "CommitComment",
    "Review",
    "Reaction",
    "Webhook", "WebhookDelivery",
    "Release", "ReleaseAsset",
    "DeployKey",
    "CommitStatus",
    "CheckRun", "CheckSuite",
    "Event",
    "IssueEvent",
    "Notification",
    "Runner", "RunnerSession", "RegistrationToken", "EnterpriseRunnerRegistrationToken",
    "Workflow", "WorkflowRun", "WorkflowJob", "Secret", "Variable", "OrgVariable", "OrgVariableRepository",
    "Gist", "GistFile",
    "SSHKey", "GPGKey",
    "FileContent", "CommitMetadata",
    "ImportJob",
    "GitHubApp", "AppInstallation", "AppInstallationToken",
    "WorkflowArtifact",
]
