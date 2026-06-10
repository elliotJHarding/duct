# Add a repo to a ticket

A ticket folder holds context. To write code against it, add a repo —
duct creates a git worktree inside the ticket folder, on a fresh branch.

See what's available — `local` rows are clones found under your
repoPaths, `remote` rows come from your GitHub orgs:

~~~~text
$ duct workspace list-repos
$repo_listing
~~~~

Pick a base branch (the default branch is used if you don't):

~~~~text
$ duct workspace list-branches $repo_name
$branch_listing
~~~~

Add it to the ticket:

~~~~text
$ duct workspace add-repo $example_key $repo_name $base_branch
~~~~

That puts a worktree at `$example_key…/$repo_name/` on a branch like
`$feature_branch` — open it with `claude` or your editor like any
checkout. Run `duct workspace add-repo` with no arguments for an
interactive picker.
