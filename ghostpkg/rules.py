"""Stable rule identifiers, and the reason objects that carry them."""

from __future__ import annotations

class Reason(str):
    """A finding's explanation, carrying the id of the rule that produced it.

    Subclassing `str` on purpose: every existing use -- printing it, searching
    it, putting it in JSON -- keeps working unchanged, while `.rule` gives the
    stable identifier that an ignore file and any machine-readable output need.
    A rule id is the prerequisite for suppression: you cannot let someone say
    "not this one" until each finding has a name that will not change.
    """

    rule: str

    def __new__(cls, rule: str, text: str) -> "Reason":
        instance = super().__new__(cls, text)
        instance.rule = rule
        return instance

    def __repr__(self) -> str:
        return f"Reason({self.rule!r}, {str(self)!r})"


#: Stable identifiers. Never renumber one: an ignore file out in the world
#: refers to these, and a shifted meaning silently changes what it suppresses.
GP_MISSING = "GP001"          # the package does not exist
GP_BAD_VERSION = "GP002"      # the pinned version does not exist
GP_RECENT = "GP003"           # published recently
GP_ONE_RELEASE = "GP004"      # a single release
GP_NO_REPO = "GP005"          # no repository or homepage link
GP_LOOKALIKE = "GP006"        # close to a popular name
GP_INSTALL_CODE = "GP007"     # something in the install script
GP_UNCHECKED = "GP008"        # the registry could not be reached
GP_NOT_INSPECTED = "GP009"    # --deep could not read the package

RULE_TITLES = {
    GP_MISSING: "package does not exist",
    GP_BAD_VERSION: "pinned version does not exist",
    GP_RECENT: "recently published",
    GP_ONE_RELEASE: "single release",
    GP_NO_REPO: "no repository link",
    GP_LOOKALIKE: "resembles a popular package",
    GP_INSTALL_CODE: "install script does something unusual",
    GP_UNCHECKED: "could not be checked",
    GP_NOT_INSPECTED: "install scripts not inspected",
}
