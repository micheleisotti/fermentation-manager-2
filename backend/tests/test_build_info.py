from fm2.build_info import read_build_info


def _repo(tmp_path, head, refs=None, packed=None):
    g = tmp_path / ".git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "HEAD").write_text(head + "\n")
    for name, sha in (refs or {}).items():
        f = g / "refs" / "heads" / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(sha + "\n")
    if packed:
        (g / "packed-refs").write_text("# pack-refs with: peeled\n" + "".join(f"{sha} {ref}\n" for ref, sha in packed.items()))
    return tmp_path


def test_branch_and_commit(tmp_path):
    root = _repo(tmp_path, "ref: refs/heads/main", refs={"main": "1a373b1c0ffee"})
    assert read_build_info(root) == {"branch": "main", "commit": "1a373b1"}


def test_branch_with_slash_from_packed_refs(tmp_path):
    root = _repo(tmp_path, "ref: refs/heads/fix/x", packed={"refs/heads/fix/x": "abcdef0123"})
    assert read_build_info(root) == {"branch": "fix/x", "commit": "abcdef0"}


def test_detached_head_and_no_repo(tmp_path):
    assert read_build_info(_repo(tmp_path / "a", "0123456789abcdef")) == {"branch": None, "commit": "0123456"}
    assert read_build_info(tmp_path / "nothing") == {"branch": None, "commit": None}


def test_worktree(tmp_path):
    main = _repo(tmp_path / "main", "ref: refs/heads/main", refs={"main": "1111111aaaa", "feat": "2222222bbbb"})
    wt_git = main / ".git" / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feat\n")
    (wt_git / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_git}\n")
    assert read_build_info(wt) == {"branch": "feat", "commit": "2222222"}
