set -e
# One layout for both cards, from trimmed masks so "centred" means the glyphs
# are centred and not their bounding box.
#   canvas 1200x630 · flower 300 tall at y=87 · wordmark 340 wide at y=477
build () {  # $1 bg  $2 ink  $3 out
  magick -size 450x392 xc:"$2" flower_trim.png -alpha off -compose CopyOpacity -composite -resize x300 f.png
  magick -size 585x112 xc:"$2" wordmark_trim.png -alpha off -compose CopyOpacity -composite -resize 340x w.png
  magick -size 1200x630 xc:"$1" \
    f.png -gravity north -geometry +0+87 -composite \
    w.png -gravity north -geometry +0+477 -composite \
    "$3"
}
build '#831531' '#D0D151' birthday_card.png
build '#F7C9DF' '#831531' invite_card_tmp.png
magick invite_card_tmp.png -quality 92 invite_card.jpg
rm -f f.png w.png invite_card_tmp.png
