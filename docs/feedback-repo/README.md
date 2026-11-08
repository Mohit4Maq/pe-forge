# pe-forge feedback

Issues for pe-forge: tool errors, rules that do not fit a real board, checks that missed something, ideas.

From your terminal, after installing pe-forge:

    forge issue --last                 # the error forge just hit, filled in
    forge issue --kind rule --rule SW-05 --note "why it does not fit"
    forge issue --kind idea --with-summary

It shows the issue first and files it with `gh`, or opens this page pre-filled. Your design stays private:
paths, net names and project names are redacted unless you add `--keep-names`.

(Seed this repository with the files in pe-forge `docs/feedback-repo/`.)
