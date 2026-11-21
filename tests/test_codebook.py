#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The codebook: names, tree, renaming.

The hierarchy is given by a dot in the name. What's mainly
checked is what breaks when working by hand: rename a branch —
the subtree must move with it rather than be orphaned.
"""

import unittest


from alumenans.codebook import (
    Code,
    ancestors,
    build_tree,
    code_key,
    is_under,
    normalize_name,
    parent_name,
    rename_in_tree,
    sort_codes,
)


class NameTests(unittest.TestCase):
    def test_spaces_around_the_dot_do_not_matter(self):
        self.assertEqual(normalize_name(" деньги . нехватка "), "деньги.нехватка")

    def test_an_empty_level_is_dropped(self):
        self.assertEqual(normalize_name("деньги..нехватка"), "деньги.нехватка")

    def test_case_and_yo_do_not_matter_for_comparison(self):
        self.assertEqual(code_key("Отписка"), code_key("отписка"))
        self.assertEqual(code_key("причём"), code_key("причем"))

    def test_the_branch_of_a_code_is_read_from_its_name(self):
        self.assertEqual(parent_name("деньги.нехватка.внезапная"), "деньги.нехватка")
        self.assertEqual(parent_name("деньги"), "")

    def test_all_branches_above_are_known(self):
        self.assertEqual(
            ancestors("деньги.нехватка.внезапная"), ["деньги", "деньги.нехватка"]
        )


class BranchTests(unittest.TestCase):
    def test_a_branch_includes_what_lies_under_it(self):
        # Clicking «деньги» ("money"), the person wants to see
        # «деньги.нехватка» ("money.shortage") too.
        self.assertTrue(is_under("деньги.нехватка", "деньги"))
        self.assertTrue(is_under("деньги", "деньги"))

    def test_a_similar_beginning_is_not_a_branch(self):
        self.assertFalse(is_under("деньгизнаки", "деньги"))
        self.assertFalse(is_under("деньги", "деньги.нехватка"))

    def test_renaming_a_branch_moves_the_whole_subtree(self):
        self.assertEqual(
            rename_in_tree("деньги.нехватка", "деньги", "финансы"), "финансы.нехватка"
        )
        self.assertEqual(rename_in_tree("деньги", "деньги", "финансы"), "финансы")

    def test_a_code_outside_the_branch_is_left_alone(self):
        self.assertEqual(rename_in_tree("страх", "деньги", "финансы"), "страх")


class TreeTests(unittest.TestCase):
    CODES = [
        Code("c1", "деньги.нехватка", "не хватило на подписку"),
        Code("c2", "страх"),
        Code("c3", "деньги"),
    ]

    def test_codes_come_out_in_the_order_of_the_tree(self):
        names = [code.name for code in sort_codes(self.CODES)]
        self.assertEqual(names, ["деньги", "деньги.нехватка", "страх"])

    def test_a_branch_nobody_created_is_still_shown(self):
        # «деньги» exists, since «деньги.нехватка» exists.
        tree = build_tree([Code("c1", "деньги.нехватка")])
        self.assertEqual([line["name"] for line in tree],
                         ["деньги", "деньги.нехватка"])
        self.assertEqual(tree[0]["origin"], "ветка")

    def test_the_depth_shows_where_to_indent(self):
        tree = build_tree(self.CODES)
        depths = {line["name"]: line["depth"] for line in tree}
        self.assertEqual(depths["деньги"], 0)
        self.assertEqual(depths["деньги.нехватка"], 1)

    def test_only_the_last_part_is_shown_in_the_tree(self):
        tree = build_tree(self.CODES)
        leaves = {line["name"]: line["leaf"] for line in tree}
        self.assertEqual(leaves["деньги.нехватка"], "нехватка")

    def test_uses_of_a_branch_include_its_children(self):
        tree = build_tree(self.CODES, uses={"c1": 4, "c3": 1})
        by_name = {line["name"]: line for line in tree}
        self.assertEqual(by_name["деньги"]["uses"], 1)
        self.assertEqual(by_name["деньги"]["uses_with_children"], 5)
        self.assertEqual(by_name["страх"]["uses_with_children"], 0)

    def test_an_empty_codebook_is_not_an_error(self):
        self.assertEqual(build_tree([]), [])


if __name__ == "__main__":
    unittest.main()
