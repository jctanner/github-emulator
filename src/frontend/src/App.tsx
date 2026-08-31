import {BrowserRouter, Navigate, Route, Routes} from "react-router-dom";

import {SessionProvider} from "./auth/SessionContext";
import {AppShell} from "./components/AppShell";
import {RepositoryLayout} from "./components/RepositoryLayout";
import {DashboardPage} from "./pages/DashboardPage";
import {BranchesPage} from "./pages/BranchesPage";
import {ActionJobPage} from "./pages/ActionJobPage";
import {ActionRunPage} from "./pages/ActionRunPage";
import {ActionsPage} from "./pages/ActionsPage";
import {AdminPage} from "./pages/AdminPage";
import {CodeBrowserPage} from "./pages/CodeBrowserPage";
import {CommitPage} from "./pages/CommitPage";
import {CommitsPage} from "./pages/CommitsPage";
import {IssueDetailPage} from "./pages/IssueDetailPage";
import {IssuesPage} from "./pages/IssuesPage";
import {FileEditorPage} from "./pages/FileEditorPage";
import {LoginPage} from "./pages/LoginPage";
import {NewIssuePage} from "./pages/NewIssuePage";
import {NewPullPage} from "./pages/NewPullPage";
import {NewRepositoryPage} from "./pages/NewRepositoryPage";
import {NotFoundPage} from "./pages/NotFoundPage";
import {ProfilePage} from "./pages/ProfilePage";
import {PullDetailPage} from "./pages/PullDetailPage";
import {PullCommitsPage} from "./pages/PullCommitsPage";
import {PullFilesPage} from "./pages/PullFilesPage";
import {PullsPage} from "./pages/PullsPage";
import {RepositoryPage} from "./pages/RepositoryPage";
import {RunnersPage} from "./pages/RunnersPage";
import {SearchPage} from "./pages/SearchPage";
import {SettingsPage} from "./pages/SettingsPage";
import {TagsPage} from "./pages/TagsPage";

export function App() {
  return (
    <BrowserRouter basename="/ui">
      <SessionProvider>
        <AppShell>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/new" element={<NewRepositoryPage />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/_admin" element={<AdminPage />} />
            <Route path="/_admin/:section" element={<AdminPage />} />
            <Route path="/:owner/:repo" element={<RepositoryLayout />}>
              <Route index element={<RepositoryPage />} />
              <Route path="issues" element={<IssuesPage />} />
              <Route
                path="labels"
                element={
                  <Navigate replace relative="path" to="../settings/labels" />
                }
              />
              <Route path="issues/new" element={<NewIssuePage />} />
              <Route path="issues/:number" element={<IssueDetailPage />} />
              <Route path="pulls" element={<PullsPage />} />
              <Route path="pulls/new" element={<NewPullPage />} />
              <Route path="pulls/:number" element={<PullDetailPage />} />
              <Route
                path="pulls/:number/commits"
                element={<PullCommitsPage />}
              />
              <Route path="pulls/:number/files" element={<PullFilesPage />} />
              <Route path="actions" element={<ActionsPage />} />
              <Route path="actions/runs/:runId" element={<ActionRunPage />} />
              <Route path="actions/jobs/:jobId" element={<ActionJobPage />} />
              <Route path="actions/runners" element={<RunnersPage />} />
              <Route path="settings/*" element={<SettingsPage />} />
              <Route path="tree/:ref/*" element={<CodeBrowserPage />} />
              <Route path="blob/:ref/*" element={<CodeBrowserPage blob />} />
              <Route path="new/:ref/*" element={<FileEditorPage create />} />
              <Route path="edit/:ref/*" element={<FileEditorPage />} />
              <Route path="commits/:ref" element={<CommitsPage />} />
              <Route path="commit/:sha" element={<CommitPage />} />
              <Route path="branches" element={<BranchesPage />} />
              <Route path="tags" element={<TagsPage />} />
            </Route>
            <Route path="/:owner" element={<ProfilePage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </AppShell>
      </SessionProvider>
    </BrowserRouter>
  );
}
