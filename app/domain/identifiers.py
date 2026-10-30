import re

#: Ids in the YAML manifests (repositories, workflows, abilities, runners): lowercase words
#: joined by - or _.
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
