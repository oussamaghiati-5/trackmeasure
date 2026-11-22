# Build the paper in a TeX Live container (host lacks the acmart fonts).
IMAGE ?= texlive/texlive:latest

pdf:
	docker run --rm --network none -u $$(id -u):$$(id -g) -e HOME=/tmp \
	  -v "$(CURDIR)":/paper -w /paper $(IMAGE) \
	  latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex

clean:
	rm -f *.aux *.bbl *.blg *.log *.out *.fls *.fdb_latexmk *.cut main.pdf
