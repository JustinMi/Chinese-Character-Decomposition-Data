#### dictionary.txt keys:

- __character:__ The Unicode character for this glyph. Required.

- __definition:__ A String definition targeted towards second-language
  learners. Optional.

- __pinyin__ A comma-separated list of String pronunciations of this character.
  Required, but may be empty.

- __decomposition:__ An [Ideograph Description Sequence](https://en.wikipedia.org/wiki/Chinese_character_description_languages#Ideographic_Description_Sequences)
  decomposition of the character. Required, but invalid if it starts with a question mark '?'.

    Note that even if the first character is a
    proper IDS symbol, any component within the decomposition may be a 
    question mark as well. For example, if we have a decomposition of a
    character into a top and bottom component but can only recognize the top
    component, we might have a decomposition like so: '⿱逢?'

- __etymology:__ An etymology for the character. This field may be null. If
  present, it will always have a "type" field, which will be one of
  "ideographic", "pictographic", or "pictophonetic".
  If the type is one of the first two options, then the etymology will
  always include a string "hint" field explaining its formation.

  If the type is "pictophonetic", then the etymology will contain three
  other fields: "hint", "phonetic", and "semantic", each of which is
  a string and each of which may be null. The etymology should be read as:
      ${semantic} (${hint}) provides the meaning while ${phonetic}
      provides the pronunciation.
  with allowances for possible null values.

- __radical:__ Unicode primary radical for this character. Required.

- __matches:__
  A list of mappings from strokes of this character to strokes of its
  components, as indexed in its decomposition tree. Any given entry in
  this list may be null. If an entry is not null, it will be a list of
  indices corresponding to a path down the decomposition tree.

  This schema is a little tricky to explain without an example. Suppose
  that the character '俢' has the decomposition: '⿰亻⿱夂彡'

  The third stroke in that character belongs to the radical '夂'.
  Its match would be [1, 0]. That is, if you think of the decomposition as
  a tree, it has '⿰' at its root with two children '亻' and '⿱', and
  '⿱' further has two children '夂' and '彡'. The path down the tree
  to '夂' is to take the second child of '⿰' and the first of '⿱',
  hence, [1, 0].

  This field can be used to generate visualizations marking each component
  within a given character, or potentially for more exotic purposes.
