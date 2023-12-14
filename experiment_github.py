import os

from githubkit import GitHub
from marko.ext.gfm import gfm

github = GitHub(os.getenv("pat"), base_url="https://git.dhl.com/api/v3")
counter = 0
for pr in github.paginate(github.rest.pulls.list, owner="SHARK-ITR-3738", repo="shark-frontend-erm", state="all"):
    if pr.body and pr.labels:
        test = gfm.parse(pr.body)
    foo = 1

foo = github.rest.pulls.list(owner="SHARK-ITR-3738", repo="shark-frontend-erm", state="all", per_page=100)

i = 2
