from aidevteam import gitops


def test_head_and_commits_since(repo):
    base = gitops.head_sha(repo)
    (repo / "x.txt").write_text("1")
    gitops.commit_all(repo, "feat: x", "aidevteam-agent", "agent@aidevteam.local")
    new = gitops.commits_since(repo, base)
    assert len(new) == 1 and new[0][1] == "feat: x"
    assert gitops.commits_since(repo, gitops.head_sha(repo)) == []


def test_dirty_and_changed(repo):
    (repo / "specs" / "inbox" / "D-001-x.md").write_text("draft")
    assert gitops.dirty_paths(repo) == ["specs/inbox/D-001-x.md"]
    base = gitops.head_sha(repo)
    gitops.commit_paths(repo, ["specs/inbox/D-001-x.md"], "заявка", "U", "u@e.com")
    assert "specs/inbox/D-001-x.md" in gitops.changed_since(repo, base)


def test_commit_author(repo):
    (repo / "y.txt").write_text("1")
    gitops.commit_all(repo, "msg", "aidevteam-agent", "agent@aidevteam.local")
    author = gitops.run_git(repo, "log", "-1", "--format=%an <%ae>")
    assert author.strip() == "aidevteam-agent <agent@aidevteam.local>"


def test_try_push_without_remote(repo):
    assert gitops.try_push(repo).startswith("skipped")
