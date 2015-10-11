#!/usr/bin/python

# The purpose of this script is only to allow to parse SVN (could be extended to GIT quite easily).
# Redmine supports this by default, so in case you wonder why I did this, there are two reasons to use this script:
# * Administrative: Your administrators don't have the time/will to set it up
# * Security: You do not want to give SCM access to your redmine server

import redmine
import time
import pysvn
import os
import sys
import re
import argparse
import unittest
import json
import logging

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s | %(levelname)8s | %(filename).4s:%(lineno)4d | %(message)s",
                    datefmt='%m-%d %H:%M:%S',
                    )

# === CRAZY REGEXES STUFF ===
# These regexes are maybe a little bit long but they are the ones handling all the magic.
# If anyone is willing to simplify them (with them still passing the unit tests), I'm very interested.

# (?im)((?:(?:re)?solved|fixed)\s*)?(?:issue|bug|feature|improvement|redmine|the):?\s*#?([0-9]+)
# (?im)((?:solved|fixed)\s*)?(?:issue|bug|feature|improvement|redmine|the):?\s*#?([0-9]+)
# (?i)(solved|solves|solving|fixed|fixes|fixing|closed|closing)?\s*(?:issue(?:s/)?|bug|feature|improvement|redmine|the):?\s*#?([0-9]+)\s*(solved|solving|fixed|fixing|closed|closing)
# (?i)(solved|solves|solving|fixed|fixes|fixing|closed|closing)?\s*(?:issue(?:s/)?|bug|feature|improvement|redmine|the):?\s*?([0-9]+)\s*(solved|solving|fixed|fixing|closed|closing)?
# (?i)(solved|solves|solving|fixed|fixes|fixing|closed|closing)?\s*(?:issue(?:s/)?|bug|feature|improvement|redmine|the):?\s*?\#?([0-9]+)\s*(solved|solving|fixed|fixing|closed|closing)?
# (?i)(solves|solving|solved|fix|fixes|fixing|fixed|closed|closing)?\s*(?:(?:(?:issue(?:s/)?|bug|feature|improvement|redmine|the)\s*)|\#)([0-9]+)\s*(solves|solving|solved|fix|fixes|fixing|fixed|closed|closing)?
pattern_text_status = "(open|opening|opened|solves|solving|solved|fix|fixes|fixing|fixed|closing|closed)"
pattern_text_bugs = r"(?i)" + pattern_text_status + "?\s*(?:(?:(?:issue(?:s\/)?|bug|feature|improvement|redmine|the)\s*)|\#)(?:\#?([0-9]+))\s*" + pattern_text_status + "?"

pattern_bugs = re.compile(pattern_text_bugs)
pattern_done = re.compile(r"(?i)(?:(?P<per1>[0-9]+)%\sdone)|(?:(?:done|did)\s(?P<per2>[0-9]+)%)")
pattern_hours = re.compile(r"(?i)estimated\s(?:to\s)?([0-9]+)\s?h(?:ours)?")
pattern_priority = re.compile(r"(?i)(very low|low|normal|high|urgent|immediate) priority")
pattern_include_diff = re.compile(r"(?i)(?:include|with|want) (?:a\s)?diff")

# print "Pattern: "+pattern_text_bugs

status_attr_to_id = {
    'opening': 2,
    'opened': 2,
    'solves': 2,
    'solved': 3,
    'solving:': 3,
    'fix': 3,
    'fixes': 3,
    'fixed': 3,
    'fixing': 3,
    'closed': 3,
    'closing': 3,
}


#
def get_priority_to_id():
    """Returns a dictionary to convert textual a priority name to priority_id"""
    return {
        "very low": 10,
        "low": 3,
        "normal": 4,
        "high": 5,
        "urgent": 6,
        "immediate": 7
    }
    # The following implementation unfortunately doesn't work and
    filename = "priority_to_id.json"
    if os.path.exists(filename):
        return json.loads(open(filename).read())
    priority_to_id = {}

    for p in rm.enumerations.filter(resource="time_entry_activities"):
        priority_to_id[p.name] = p.id

    open(filename + ".tmp").write(json.dumps(priority_to_id))
    os.rename(filename + ".tmp", filename)

    return priority_to_id


def handle_log(message, author=None, rev=None, date=None):
    matches = re.findall(pattern_bugs, message)
    if matches:
        issues_attr = {}
        for m in matches:
            issue_nb = m[1]
            if not issue_nb in issues_attr.keys():
                if m[0]:
                    issues_attr[issue_nb] = m[0]
                elif m[2]:
                    issues_attr[issue_nb] = m[2]
                else:
                    issues_attr[issue_nb] = ""

        # print "Matches: " + str(len(issues_attr)) + " / " + str(issues_attr)

        changes_list = {}
        for issue_id, attr in issues_attr.iteritems():
            changes = {}
            # This is only for testing (very time consuming)

            # print "      - Subject : " + issue.subject
            # print "      - Author : " + str(issue.author)

            if attr:
                changes["status_id"] = status_attr_to_id[attr.lower()]

            if len(issues_attr) == 1:
                done_match = re.findall(pattern_done, message)

                # These rules apply if we have only ONE issue
                if done_match and len(done_match) == 1:
                    v = done_match[0][0]
                    if not v:
                        v = done_match[0][1]
                    changes["done_ratio"] = int(v)

                hours_match = re.findall(pattern_hours, message)
                if hours_match and len(hours_match) == 1:
                    changes["estimated_hours"] = int(hours_match[0])

                priority_match = re.findall(pattern_priority, message)
                if priority_match and len(priority_match) == 1:
                    changes["priority_id"] = get_priority_to_id()[priority_match[0]]

            changes["notes"] = "SVN r{rev}, {date}, {author}: <pre>{message}</pre>" \
                .format(rev=rev, date=date, author=author, message=message)

            if re.findall(pattern_include_diff, message):
                # TODO: Move this somewhere else.
                # This is a bad design, we shouldn't mix the SCM messages parsing code
                # with the SVN logic around it.
                diff = pysvn.Client().diff(
                    '/tmp',
                    svn_url,
                    revision1=pysvn.Revision(pysvn.opt_revision_kind.number, rev-1),
                    url_or_path2=svn_url,
                    revision2=pysvn.Revision(pysvn.opt_revision_kind.number, rev)
                )
                changes["notes"] += "diff: <pre>"+diff+"</pre>"

            #logging.debug("Changing issue %s with %s", issue_id, json.dumps(changes))

            changes_list[issue_id] = changes

        return changes_list


def main():
    # We get a redmine connection
    rm = redmine.Redmine(redmine_url, key=redmine_key)

    if os.path.exists(".rev_prev"):
        rev_prev = int(open(".rev_prev", 'r').read())
    else:
        logging.critical("Not having the .rev_prev file is very BAD !!!")
        # sys.exit(1)


    # We list all SVN logs since last time
    rev_limit = 50
    logs = pysvn.Client().log(
        svn_url,
        revision_start=pysvn.Revision(pysvn.opt_revision_kind.number, rev_prev + 1),
        revision_end=pysvn.Revision(pysvn.opt_revision_kind.number, rev_prev + rev_limit)
    )

    last_rev = None

    for log in logs:
        author = log["author"]
        revision = log.revision.number
        date = time.ctime(log.date)
        message = log.message
        logging.info("* {revision} - {date} - {author} : {message}".format(revision=revision,
                                                                           date=date,
                                                                           author=author,
                                                                           message=message.replace("\n", ".").replace("\r", ".")))

        if not last_rev or revision > last_rev:
            last_rev = revision

        changes_by_issue = handle_log(message, author, revision, date)
        if changes_by_issue:
            logging.debug("Changes: %s", json.dumps(changes_by_issue))
            for issue_id, changes in changes_by_issue.iteritems():
                logging.info("Considering update of issue %s ...", issue_id)

                issue = rm.issue.get(issue_id)
                if not issue:
                    logging.warning("Issue %s doesn't exist !!!", issue_id)
                    break
                else:
                    msg = "SVN r{rev},".format(rev=revision)
                    for jl in issue.journals:
                        logging.debug("    Note: "+jl.notes.replace("\n", ".").replace("\r", "."))
                        if msg in jl.notes:
                            logging.warning("There's already a reference to our notes !")
                            changes = None
                            break
                if not test_only and changes:
                    logging.info("Updating issue %s ...", issue_id)
                    rm.issue.update(issue_id, **changes)

    if last_rev:
        open(".rev_prev.tmp", 'w').write(str(last_rev))
        os.rename(".rev_prev.tmp", ".rev_prev")


class TestCommitMessages(unittest.TestCase):
    def test_issue_matching_1(self):
        changes = handle_log("issue 123")
        self.assertTrue(changes.has_key("123"))

    def test_issue_matching_2(self):
        changes = handle_log("bug #123")
        self.assertTrue(changes.has_key("123"))

    def test_issue_matching_3(self):
        changes = handle_log("the #12")
        self.assertTrue(changes.has_key("12"))

    def test_issue_matching_4(self):
        changes = handle_log("about #123")
        self.assertTrue(changes.has_key("123"))

    def test_issue_matching_5(self):
        changes = handle_log("number 20 and issue 13 and #30")
        self.assertEqual(len(changes), 2)
        self.assertTrue(changes.has_key("13"))
        self.assertTrue(changes.has_key("30"))

    def test_issue_fixed_1(self):
        changes = handle_log("fixing issue 123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["status_id"], 3)

    def test_issue_fixed_2(self):
        changes = handle_log("issue 123 fixed")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["status_id"], 3)

    def test_issue_fixed_3(self):
        changes = handle_log("This commit fixes issue #123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["status_id"], 3)

    def test_issue_fixed_multi(self):
        changes = handle_log("issue #123 and #256 need to be fixed soon")
        self.assertEquals(len(changes.keys()), 2)
        self.assertFalse(changes["123"].has_key("status_id"))
        self.assertFalse(changes["256"].has_key("status_id"))

    def test_issue_opening_1(self):
        changes = handle_log("opening #123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["status_id"], 2)

    def test_issue_done_1(self):
        changes = handle_log("I've done 30% of issue #123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["done_ratio"], 30)
        self.assertEquals(len(changes.keys()), 1)

    def test_issue_done_2(self):
        changes = handle_log("30% done on issue 123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["done_ratio"], 30)
        self.assertEquals(len(changes.keys()), 1)

    def test_issue_done_3(self):
        changes = handle_log("did 30% of issue 123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["done_ratio"], 30)
        self.assertEquals(len(changes.keys()), 1)

    def test_issue_estimated_1(self):
        changes = handle_log("issue 123 was estimated to 20h of work")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["estimated_hours"], 20)
        self.assertEquals(len(changes.keys()), 1)

    def test_issue_estimated_2(self):
        changes = handle_log("issue 123 requires an estimated 20 hours of work")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["estimated_hours"], 20)
        self.assertEquals(len(changes.keys()), 1)

    def test_issue_priority_1(self):
        changes = handle_log("switching #123 to high priority")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["priority_id"], 5)

    def test_issue_priority_2(self):
        changes = handle_log("setting immediate priority on bug 123")
        self.assertTrue(changes.has_key("123"))
        self.assertEquals(changes["123"]["priority_id"], 7)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Parse SVN messages to perform some redmine actions')
    parser.add_argument('--unit-tests', action='store_true', help='Activate unit tests')
    parser.add_argument('--redmine-url', help='Redmine server URL', default=os.environ.get("REDMINE_URL"))
    parser.add_argument('--redmine-key', help='Redmine server access key', default=os.environ.get("REDMINE_KEY"))
    parser.add_argument('--svn-url', help='SVN URL', default=os.environ.get("SVN_URL"))
    parser.add_argument('--test-only', action='store_true', help='Do not update redmine')
    args = parser.parse_args()

    redmine_url = args.redmine_url
    redmine_key = args.redmine_key
    svn_url = args.svn_url
    test_only = args.test_only

    if not redmine_url:
        logging.critical("Missing the redmine URL")

    if not redmine_key:
        logging.critical("Missing the redmine key")

    if not svn_url:
        logging.critical("Missing the SVN URL")

    if args.unit_tests:
        suite = unittest.TestLoader().loadTestsFromTestCase(TestCommitMessages)
        unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        main()
