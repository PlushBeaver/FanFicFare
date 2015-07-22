# -*- coding: utf-8 -*-

# Copyright 2011 Fanficdownloader team,
#           2015 FanFicFare team,
#           2015 Dmitry Kozliuk
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime
import logging
import re
import urllib2
import codecs

from .. import BeautifulSoup as bs
from ..htmlcleanup import stripHTML
from .. import exceptions as exceptions
from base_adapter import BaseSiteAdapter, makeDate


_logger = logging.getLogger(__name__)


def getClass():
    """Returns adapter class defined in this module."""
    return MassEffect2InAdapter


class ParsingError(Exception):
    """Indicates an error while parsing web page content."""
    def __init__(self, message):
        Exception.__init__(self)
        self.message = message

    def __str__(self):
        return self.message


class MassEffect2InAdapter(BaseSiteAdapter):
    """Provides support for masseffect2.in site as story source.
    Can be used as a template for sites build upon Ucoz.com engine.
    Specializations:
        1) Russian content (date format, genre names, etc.);
        2) original `R.A.T.I.N.G.' rating scale, used by masseffect2.in
           and some affiliated sites."""

    WORD_PATTERN = re.compile(u'\w+', re.UNICODE)
    DOCUMENT_ID_PATTERN = re.compile(u'\d+-\d+-\d+-\d+')
    SITE_LANGUAGE = u'Russian'

    def __init__(self, config, url):
        BaseSiteAdapter.__init__(self, config, url)

        self.decode = ["utf8"]

        self.story.setMetadata('siteabbrev', 'me2in')
        self.story.setMetadata('storyId', self._getDocumentId(self.url))

        self._setURL(self._makeDocumentUrl(self.story.getMetadata('storyId')))

        self._chapters = {}
        self._parsingConfiguration = None

    # Must be @staticmethod, not @classmethod!
    @staticmethod
    def getSiteDomain():
        return 'www.masseffect2.in'

    @classmethod
    def getSiteExampleURLs(cls):
        return u' '.join([cls._makeDocumentUrl('19-1-0-1234'),
                          cls._makeDocumentUrl('24-1-0-4321')])

    def getSiteURLPattern(self):
        return re.escape(self._makeDocumentUrl('')) + self.DOCUMENT_ID_PATTERN.pattern

    def use_pagecache(self):
        """Allows use of downloaded page cache.  It is essential for this
        adapter, because the site does not offers chapter URL list, and many
        pages have to be fetched and parsed repeatedly."""
        return True

    def extractChapterUrlsAndMetadata(self):
        """Extracts chapter URLs and story metadata.  Actually downloads all
        chapters, which is not exactly right, but necessary due to technical
        limitations of the site."""

        def followChapters(starting, forward=True):
            if forward:
                url = starting.getNextChapterUrl()
            else:
                url = starting.getPreviousChapterUrl()
            if url:
                url = self._makeDocumentUrl(self._getDocumentId(url))
                following = self._makeChapter(url)
                if forward:
                    yield following
                for chapter in followChapters(following, forward):
                    yield chapter
                if not forward:
                    yield following

        try:
            startingChapter = self._makeChapter(self.url)
        except urllib2.HTTPError, error:
            if error.code == 404:
                raise exceptions.StoryDoesNotExist(self.url)
            raise

        try:
            self.story.setMetadata('title', startingChapter.getStoryTitle())
            self.story.setMetadata('author', startingChapter.getAuthorName())
            authorId = startingChapter.getAuthorId()
            authorUrl = 'http://%s/index/%s' % (self.getSiteDomain(), authorId)
            self.story.setMetadata('authorId', authorId)
            self.story.setMetadata('authorUrl', authorUrl)
            self.story.setMetadata('rating', startingChapter.getRatingTitle())
        except ParsingError, error:
            raise exceptions.FailedToDownload(
                u"Failed to parse story metadata for `%s': %s" % (self.url, error))

        # We only have one date for each chapter and assume the oldest one
        # to be publication date and the most recent one to be update date.
        datePublished = datetime.datetime.max
        dateUpdated = datetime.datetime.min
        wordCount = 0
        # We aim at counting chapters, not chapter parts.
        chapterCount = 0
        storyInProgress = False

        chapters = \
            list(followChapters(startingChapter, forward=False)) + \
            [startingChapter] + \
            list(followChapters(startingChapter, forward=True))

        try:
            for chapter in chapters:
                url = chapter.getUrl()
                self._chapters[url] = chapter
                _logger.debug(u"Processing chapter `%s'.", url)

                datePublished = min(datePublished, chapter.getDate())
                dateUpdated = max(dateUpdated, chapter.getDate())

                self.story.extendList('genre', chapter.getGenres())
                self.story.extendList('characters', chapter.getCharacters())

                wordCount += self._getWordCount(chapter.getTextElement())

                index = chapter.getIndex()
                if index:
                    chapterCount = max(chapterCount, index)
                else:
                    chapterCount += 1

                # Story is in progress if any chapter is in progress.
                # Some chapters may have no status attribute.
                chapterInProgress = chapter.isInProgress()
                if chapterInProgress is not None:
                    storyInProgress |= chapterInProgress

                # If any chapter is adult, consider the whole story adult.
                if chapter.isRatingAdult():
                    self.story.setMetadata('is_adult', True)

            titles = [chapter.getTitle() for chapter in chapters]
            hasNumbering = any([chapter.getIndex() is not None for chapter in chapters])
            if not hasNumbering:
                # There are stories without chapter numbering, but under single title,
                # which is heading prefix (such stories are not series).  We identify
                # common prefix for all chapters and use it as story title, trimming
                # chapter titles the length of this prefix.
                largestCommonPrefix = _getLargestCommonPrefix(*titles)
                prefixLength = len(largestCommonPrefix)
                storyTitle = re.sub(u'[:\.\s]*$', u'', largestCommonPrefix, re.UNICODE)
                self.story.setMetadata('title', storyTitle)
                for chapter in chapters:
                    self.chapterUrls.append(
                        (chapter.getTitle()[prefixLength:], chapter.getUrl()))
            else:
                # Simple processing for common cases.
                for chapter in chapters:
                    self.chapterUrls.append(
                        (chapter.getTitle(), chapter.getUrl()))

        except ParsingError, error:
                raise exceptions.FailedToDownload(
                    u"Failed to download chapter `%s': %s" % (url, error))

        # Some metadata are handled separately due to format conversions.
        self.story.setMetadata(
            'status', 'In Progress' if storyInProgress else 'Completed')
        self.story.setMetadata('datePublished', datePublished)
        self.story.setMetadata('dateUpdated', dateUpdated)
        self.story.setMetadata('numWords', str(wordCount))
        self.story.setMetadata('numChapters', chapterCount)

        # Site-specific metadata.
        self.story.setMetadata('language', self.SITE_LANGUAGE)

    def getChapterText(self, url):
        """Grabs the text for an individual chapter."""
        if url not in self._chapters:
            raise exceptions.FailedToDownload(u"No chapter `%s' present!" % url)
        chapter = self._chapters[url]
        return self.utf8FromSoup(url, chapter.getTextElement())

    def _makeChapter(self, url):
        """Creates a chapter object given a URL."""
        document = self._loadDocument(url)
        chapter = Chapter(self._getParsingConfiguration(), url, document)
        return chapter

    def _getWordCount(self, element):
        """Returns word count in plain text extracted from chapter body."""
        text = stripHTML(element)
        count = len(re.findall(self.WORD_PATTERN, text))
        return count

    def _getParsingConfiguration(self):
        if not self._parsingConfiguration:
            self._parsingConfiguration = {}

            adultRatings = self.getConfigList('adult_ratings')
            if not adultRatings:
                raise exceptions.PersonalIniFailed(
                    u"Missing `adult_ratings' setting", u"MassEffect2.in", u"?")
            adultRatings = set(adultRatings)
            self._parsingConfiguration['adultRatings'] = adultRatings

            ratingTitleDescriptions = self.getConfigList('rating_titles')
            if ratingTitleDescriptions:
                ratingTitles = {}
                for ratingDescription in ratingTitleDescriptions:
                    parts = ratingDescription.split(u'=')
                    if len(parts) < 2:
                        _logger.warning(
                            u"Invalid `rating_titles' setting, missing `=' in `%s'."
                            % ratingDescription)
                        continue
                    labels = parts[:-1]
                    title = parts[-1]
                    for label in labels:
                        ratingTitles[label] = title
                        # Duplicate label aliasing in adult rating set.
                        if label in adultRatings:
                            adultRatings.add(*labels)
                self._parsingConfiguration['adultRatings'] = list(adultRatings)
                self._parsingConfiguration['ratingTitles'] = ratingTitles
            else:
                raise exceptions.PersonalIniFailed(
                    u"Missing `rating_titles' setting", u"MassEffect2.in", u"?")

            self._parsingConfiguration['needsChapterNumbering'] = \
                self.getConfig('strip_chapter_numbers', False) \
                and not self.getConfig('add_chapter_numbers', False)

            self._parsingConfiguration['excludeEditorSignature'] = \
                self.getConfig('exclude_editor_signature', False)

        return self._parsingConfiguration

    def _getDocumentId(self, url):
        """Extracts document ID from MassEffect2.in URL."""
        match = re.search(self.DOCUMENT_ID_PATTERN, url)
        if not match:
            raise ValueError(u"Failed to extract document ID from `'" % url)
        documentId = url[match.start():match.end()]
        return documentId

    @classmethod
    def _makeDocumentUrl(cls, documentId):
        """Makes a chapter URL given a chapter ID."""
        return 'http://%s/publ/%s' % (cls.getSiteDomain(), documentId)

    def _loadDocument(self, url):
        """Fetches URL content and returns its element tree
        with parsing settings tuned for MassEffect2.in."""
        return bs.BeautifulStoneSoup(
            self._fetchUrl(url), selfClosingTags=('br', 'hr', 'img'))

    def _fetchUrl(self, url,
                  parameters=None,
                  usecache=True,
                  extrasleep=None):
        """Fetches URL contents, see BaseSiteAdapter for details.
        Overridden to support on-disk cache when debugging Calibre."""
        from calibre.constants import DEBUG
        if DEBUG:
            import os
            documentId = self._getDocumentId(url)
            path = u'./cache/%s' % documentId
            if os.path.isfile(path) and os.access(path, os.R_OK):
                _logger.debug(u"On-disk cache HIT for `%s'.", url)
                with codecs.open(path, encoding='utf-8') as input:
                    return input.read()
            else:
                _logger.debug(u"On-disk cache MISS for `%s'.", url)

        content = BaseSiteAdapter._fetchUrl(
            self, url, parameters, usecache, extrasleep)

        if DEBUG:
            import os
            if os.path.isdir(os.path.dirname(path)):
                _logger.debug(u"Caching `%s' content on disk.", url)
                with codecs.open(path, mode='w', encoding='utf-8') as output:
                    output.write(content)

        return content


class Chapter(object):
    """Represents a lazily-parsed chapter of a story."""
    def __init__(self, configuration, url, document):
        self._configuration = configuration
        self._url = url
        self._document = document
        # Lazy-loaded:
        self._parsedHeading = None
        self._date = None
        self._author = None
        self._attributes = None
        self._textElement = None
        self._infoBar = None

    def getIndex(self):
        parsedHeading = self._getHeading()
        if 'chapterIndex' in parsedHeading:
            return parsedHeading['chapterIndex']

    def getPartIndex(self):
        parsedHeading = self._getHeading()
        if 'partIndex' in parsedHeading:
            return parsedHeading['partIndex']

    def getStoryTitle(self):
        return self._getHeading()['storyTitle']

    def getTitle(self):
        return self._getHeading()['chapterTitle']

    def getAuthorId(self):
        return self._getAuthor()['id']

    def getAuthorName(self):
        return self._getAuthor()['name']

    def getDate(self):
        return self._getDate()

    def getRatingTitle(self):
        return self._getAttributes()['rating']['title']

    def isRatingAdult(self):
        return self._getAttributes()['rating']['isAdult']

    def getCharacters(self):
        attributes = self._getAttributes()
        if 'characters' in attributes:
            return attributes['characters']
        return []

    def getGenres(self):
        attributes = self._getAttributes()
        if 'genres' in attributes:
            return attributes['genres']
        return []

    def isInProgress(self):
        attributes = self._getAttributes()
        if 'isInProgress' in attributes:
            return attributes['isInProgress']

    def getUrl(self):
        return self._url

    def getTextElement(self):
        return self._getTextElement()

    def getPreviousChapterUrl(self):
        """Downloads chapters following `Previous chapter' links.
        Returns a list of chapters' URLs."""
        return self._getSiblingChapterUrl({'class': 'fl tal'})

    def getNextChapterUrl(self):
        """Downloads chapters following `Next chapter' links.
        Returns a list of chapters' URLs."""
        return self._getSiblingChapterUrl({'class': 'tar fr'})

        else:
            return storyTitle != thisStoryTitle

    CHAPTER_NUMBER_PATTERN = re.compile(
        u'''[\.:\s]*
            (?:глава)?  # `Chapter' in Russian.
            \s
            (?:(?P<chapterIndex>\d{1,3})(?=\D|$))
            (?:
              (?:
                # For `X.Y' and `X-Y' numbering styles:
                [\-\.]|
                # For `Chapter X (part Y)' and similar numbering styles:
                [\.,]?\s
                (?P<brace>\()?
                (?:часть)?      # `Part' in Russian.
                \s
              )
              (?P<partIndex>\d{1,3})
              (?(brace)\))
            )?
            [\.:\s]*
         ''',
        re.IGNORECASE + re.UNICODE + re.VERBOSE)

    PROLOGUE_EPILOGUE_PATTERN = re.compile(
        u'''[\.:\s]*         # Optional separators.
            (пролог|эпилог)  # `Prologue' or `epilogue' in Russian.
            [\.:\s]*         # Optional separators.
         ''',
        re.IGNORECASE + re.UNICODE + re.VERBOSE)

    def _getHeading(self):
        if not self._parsedHeading:
            self._parsedHeading = self._parseHeading()
        return self._parsedHeading

    def _parseHeading(self):
        """Extracts meaningful parts from full chapter heading with.
        Returns a dictionary containing `storyTitle', `chapterTitle'
        (including numbering if allowed by settings, may be the same as
        `storyTitle' for short stories), `chapterIndex' (optional, may be
        zero), and `partIndex' (optional, chapter part, may be zero).
        When no dedicated chapter title is present, generates one based on
        chapter and part indices.  Correctly handles `prologue' and `epilogue'
        cases."""
        try:
            heading = stripHTML(
                self._document.find('div', {'class': 'eTitle'}).string)
        except AttributeError:
            raise ParsingError(u'Failed to locate title.')

        match = re.search(self.CHAPTER_NUMBER_PATTERN, heading)
        if match:
            chapterIndex = int(match.group('chapterIndex'))
            # There are cases with zero chapter or part number (e. g.:
            # numbered prologue, not to be confused with just `Prologue').
            if match.group('partIndex'):
                partIndex = int(match.group('partIndex'))
            else:
                partIndex = None
            chapterTitle = heading[match.end():].strip()
            if chapterTitle:
                if self._configuration['needsChapterNumbering']:
                    if partIndex is not None:
                        title = u'%d.%d. %s' % \
                                (chapterIndex, partIndex, chapterTitle)
                    else:
                        title = u'%d. %s' % (chapterIndex, chapterTitle)
                else:
                    title = chapterTitle
            else:
                title = u'Глава %d' % chapterIndex
                if partIndex:
                    title += u' (часть %d)' % partIndex

            # For seldom found cases like `Story: prologue and chapter 1'.
            storyTitle = heading[:match.start()]
            match = re.search(self.PROLOGUE_EPILOGUE_PATTERN, storyTitle)
            if match:
                matches = list(
                    re.finditer(u'[:\.]', storyTitle))
                if matches:
                    realStoryTitleEnd = matches[-1].start()
                    if realStoryTitleEnd >= 0:
                        storyTitle = storyTitle[:realStoryTitleEnd]
                    else:
                        _logger.warning(
                            u"Title contains `%s', suspected to be part of "
                            u"numbering, but no period (`.') before it.  "
                            u"Full title is preserved." % storyTitle)

            self._parsedHeading = {
                'storyTitle': unicode(storyTitle),
                'chapterTitle': unicode(title),
                'chapterIndex': chapterIndex
            }
            if partIndex is not None:
                self._parsedHeading['partIndex'] = partIndex
            return self._parsedHeading

        match = re.search(self.PROLOGUE_EPILOGUE_PATTERN, heading)
        if match:
            storyTitle = heading[:match.start()]
            chapterTitle = heading[match.end():].strip()
            matchedText = heading[match.start():match.end()]
            if chapterTitle:
                title = u'%s. %s' % (matchedText, chapterTitle)
            else:
                title = matchedText
            self._parsedHeading = {
                'storyTitle': unicode(storyTitle),
                'chapterTitle': unicode(title)
            }
            return self._parsedHeading

        self._parsedHeading = {
            'storyTitle': unicode(heading),
            'chapterTitle': unicode(heading)
        }
        return self._parsedHeading

    def _getAuthor(self):
        if not self._author:
            self._author = self._parseAuthor()
        return self._author

    def _parseAuthor(self):
        try:
            authorLink = self._getInfoBarElement() \
                .find('i', {'class': 'icon-user'}) \
                .findNextSibling('a')
        except AttributeError:
            raise ParsingError(u'Failed to locate author link.')
        match = re.search(u'(8-\d+)', authorLink['onclick'])
        if not match:
            raise ParsingError(u'Failed to extract author ID.')
        authorId = match.group(0)
        authorName = stripHTML(authorLink.text)
        return {
            'id': authorId,
            'name': authorName
        }

    def _getDate(self):
        if not self._date:
            self._date = self._parseDate()
        return self._date

    def _parseDate(self):
        try:
            dateText = self._getInfoBarElement() \
                .find('i', {'class': 'icon-eye'}) \
                .findPreviousSibling(text=True) \
                .strip(u'| \n')
        except AttributeError:
            raise ParsingError(u'Failed to locate date.')
        date = makeDate(dateText, '%d.%m.%Y')
        return date

    def _getInfoBarElement(self):
        if not self._infoBar:
            self._infoBar = self._document.find('td', {'class': 'eDetails2'})
            if not self._infoBar:
                raise ParsingError(u'No informational bar found.')
        return self._infoBar

    def _getAttributes(self):
        if not self._attributes:
            self._attributes = self._parseAttributes()
        return self._attributes

    def _parseAttributes(self):
        attributes = {}
        try:
            elements = self._document \
                .find('div', {'class': 'comm-div'}) \
                .findNextSibling('div', {'class': 'cb'}) \
                .nextGenerator()
            attributesText = u''
            for element in elements:
                if not element:
                    _logger.warning(u'Attribute block not terminated!')
                    break
                if isinstance(element, bs.Tag):
                    # Although deprecated, `has_key()' is required here.
                    if element.name == 'div' and \
                            element.has_key('class') and \
                            element['class'] == 'cb':
                        break
                    elif element.name == 'img':
                        rating = self._parseRatingFromImage(element)
                        if rating:
                            attributes['rating'] = rating
                else:
                    attributesText += stripHTML(element)
        except AttributeError or TypeError:
            raise ParsingError(u'Failed to locate and collect attributes.')

        for record in re.split(u';|\.', attributesText):
            parts = record.split(u':', 1)
            if len(parts) < 2:
                continue
            key = parts[0].strip().lower()
            value = parts[1].strip().strip(u'.')
            parsed = self._parseAttribute(key, value)
            if parsed:
                attributes[parsed[0]] = parsed[1]

        if 'rating' not in attributes:
            raise ParsingError(u'Failed to locate or recognize rating!')

        return attributes

    RATING_LABEL_PATTERN = re.compile(u'/(?P<rating>[ERATINnG]+)\.png$')

    def _parseRatingFromImage(self, element):
        """Given an image element, tries to parse story rating from it."""
        # Although deprecated, `has_key()' is required here.
        if not element.has_key('src'):
            return
        source = element['src']
        if 'REITiNG' in source:
            match = re.search(self.RATING_LABEL_PATTERN, source)
            if not match:
                return
            label = match.group('rating')
            if label in self._configuration['ratingTitles']:
                return {
                    'label': label,
                    'title': self._configuration['ratingTitles'][label],
                    'isAdult': label in self._configuration['adultRatings']
                }
            else:
                _logger.warning(u"No title found for rating label `%s'!" % label)
        # FIXME: It seems, rating has to be optional due to such URLs.
        elif source == 'http://www.masseffect2.in/_fr/10/1360399.png':
            label = 'Nn'
            return {
                'label': 'Nn',
                'title': self._configuration['ratingTitles'][label],
                'isAdult': label in self._configuration['adultRatings']
            }

    # Various `et cetera' and `et al' forms in Russian texts.
    # Intended to be used with whole strings!
    ETC_PATTERN = re.compile(
        u'''[и&]\s(?:
              (?:т\.?\s?[пд]\.?)|
              (?:др(?:угие|\.)?)|
              (?:пр(?:очие|\.)?)|
              # Note: identically looking letters `K' and `o'
              # below are from Latin and Cyrillic alphabets.
              (?:ко(?:мпания)?|[KК][oо°])
            )$
        ''',
        re.IGNORECASE + re.UNICODE + re.VERBOSE)

    def _parseAttribute(self, key, value):
        """Parses a single known attribute value for chapter metadata."""

        def refineCharacter(name):
            """Refines character name from stop-words and distortions."""
            strippedName = name.strip()
            nameOnly = re.sub(self.ETC_PATTERN, u'', strippedName)
            # TODO: extract canonical name (even ME-specific?).
            canonicalName = nameOnly
            return canonicalName

        if re.match(u'жанры?', key, re.UNICODE):
            definitions = value.split(u',')
            if len(definitions) > 4:
                _logger.warning(u'Possibly incorrect genre detection!')
            genres = []
            for definition in definitions:
                genres += definition.split(u'/')
            return 'genres', genres
        elif key == u'статус':
            isInProgress = value == u'в процессе'
            return 'isInProgress', isInProgress
        elif key == u'персонажи':
            characters = [refineCharacter(name) for name in value.split(u',')]
            return 'characters', characters
        else:
            _logger.debug(u"Unrecognized attribute `%s' ignored.", key)

    def _getTextElement(self):
        if not self._textElement:
            self._textElement = self.__collectTextElements()
        return self._textElement

    def __collectTextElements(self):
        """Returns all elements containing parts of chapter text (which may be
        <p>aragraphs, <div>isions or plain text nodes) under a single root."""
        starter = self._document.find('div', {'id': u'article'})
        if starter is None:
            # FIXME: This will occur if the method is called more than once.
            # The reason is elements appended to `root' are removed from
            # the document. BS 4.4 implements cloning via `copy.copy()',
            # but supporting it for earlier versions is error-prone
            # (due to relying on BS internals).
            raise ParsingError(u'Failed to locate text.')
        collection = [starter]
        for element in starter.nextSiblingGenerator():
            if element is None:
                break
            if isinstance(element, bs.Tag) and element.name == 'tr':
                break
            collection.append(element)
        root = bs.Tag(self._document, 'td')
        for element in collection:
            root.append(element)

        if self._configuration['excludeEditorSignature']:
            root = self._excludeEditorSignature(root)

        return root

    def _getSiblingChapterUrl(self, selector):
        """Downloads chapters one by one by locating and following links
        specified by a selector.  Returns chapters' URLs in order they
        were found."""
        block = self._document\
            .find('td', {'class': 'eDetails1'})\
            .find('div', selector)
        if not block:
            return
        link = block.find('a')
        if not link:
            return
        return link['href']

    SIGNED_PATTERN = re.compile(u'отредактирова(?:но|ла?)[:.\s]', re.IGNORECASE + re.UNICODE)

    def _excludeEditorSignature(self, root):
        for textNode in root.findAll(text=True):
            if re.match(self.SIGNED_PATTERN, textNode.string):
                editorLink = textNode.findNext('a')
                if editorLink:
                    editorLink.extract()
                # Seldom editor link has inner formatting, which is sibling DOM-wise.
                editorName = textNode.findNext('i')
                if editorName:
                    editorName.extract()
                textNode.extract()
                # We could try removing container element, but there is a risk
                # of removing text ending with it.  Better play safe here.
                break
        return root


def _getLargestCommonPrefix(*args):
    """Returns largest common prefix of all unicode(!) arguments.
    :rtype : unicode
    """
    from itertools import takewhile, izip
    allSame = lambda xs: len(set(xs)) == 1
    return u''.join([i[0] for i in takewhile(allSame, izip(*args))])
