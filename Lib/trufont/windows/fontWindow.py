from PyQt5.QtCore import QEvent, QMimeData, QSize, QStandardPaths, Qt
from PyQt5.QtGui import QColor, QKeySequence, QPainter, QPainterPath
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QMessageBox, QShortcut,
    QStackedWidget, QVBoxLayout)
from defconQt.controls.glyphCellView import GlyphCellView
from defconQt.windows.baseWindows import BaseWindow
from trufont.controls.fileMessageBoxes import CloseMessageBox, ReloadMessageBox
from trufont.controls.fontDialogs import AddGlyphsDialog, SortDialog
from trufont.controls.fontStatusBar import FontStatusBar
from trufont.controls.glyphCanvasView import GlyphCanvasView
from trufont.controls.glyphDialogs import FindDialog
from trufont.controls.propertiesView import PropertiesView
from trufont.controls.tabWidget import TabWidget
from trufont.controls.toolBar import ToolBar
from trufont.objects import settings
from trufont.objects.menu import Entries
from trufont.tools import errorReports, platformSpecific
from trufont.tools.uiMethods import deleteUISelection, removeUIGlyphElements
from trufont.windows.fontFeaturesWindow import FontFeaturesWindow
from trufont.windows.fontInfoWindow import FontInfoWindow
from trufont.windows.groupsWindow import GroupsWindow
from trufont.windows.metricsWindow import MetricsWindow
from ufoLib.glifLib import readGlyphFromString
from collections import OrderedDict
import os
import pickle

_path = QPainterPath()
_path.moveTo(5, 8)
_path.lineTo(23, 8)
_path.lineTo(23, 10)
_path.lineTo(5, 10)
_path.closeSubpath()
_path.moveTo(5, 13)
_path.lineTo(23, 13)
_path.lineTo(23, 15)
_path.lineTo(5, 15)
_path.closeSubpath()
_path.moveTo(5, 18)
_path.lineTo(23, 18)
_path.lineTo(23, 20)
_path.lineTo(5, 20)
_path.closeSubpath()


class FontWindow(BaseWindow):

    def __init__(self, font, parent=None):
        super().__init__(parent)
        self._font = None

        self._infoWindow = None
        self._featuresWindow = None
        self._metricsWindow = None
        self._groupsWindow = None

        self.toolBar = ToolBar(self)
        self.toolBar.setTools(QApplication.instance().drawingTools())

        self.glyphCellView = GlyphCellView(self)
        self.glyphCellView.glyphActivated.connect(self.openGlyphTab)
        self.glyphCellView.glyphsDropped.connect(self._orderChanged)
        self.glyphCellView.selectionChanged.connect(self._selectionChanged)
        self.glyphCellView.setAcceptDrops(True)
        self.glyphCellView.setCellRepresentationName("TruFont.GlyphCell")
        # TODO: check this on non-Windows platforms
        self.glyphCellView.setFrameShape(self.glyphCellView.NoFrame)
        self.glyphCellView.setFocus()

        self.tabWidget = TabWidget(self)
        self.tabWidget.setHeroFirstTab(True)
        self.tabWidget.addTab(self.tr("Font"))

        self.stackWidget = QStackedWidget(self)
        self.stackWidget.addWidget(self.glyphCellView)
        self.tabWidget.currentTabChanged.connect(self._tabChanged)
        self.tabWidget.tabRemoved.connect(
            lambda index: self.stackWidget.removeWidget(
                self.stackWidget.widget(index)))
        self.stackWidget.currentChanged.connect(self._widgetChanged)

        self.propertiesView = PropertiesView(self)
        self.propertiesView.hide()

        self.statusBar = FontStatusBar()
        self.statusBar.setMinimumSize(32)
        self.statusBar.setMaximumSize(128)
        self.statusBar.sizeChanged.connect(self._sizeChanged)

        self.setFont_(font)

        app = QApplication.instance()
        app.dispatcher.addObserver(
            self, "_drawingToolRegistered", "drawingToolRegistered")
        app.dispatcher.addObserver(
            self, "_drawingToolUnregistered", "drawingToolUnregistered")
        app.dispatcher.addObserver(
            self, "_glyphViewGlyphChanged", "glyphViewGlyphChanged")

        layout = QHBoxLayout(self)
        layout.addWidget(self.toolBar)
        vLayout = QVBoxLayout()
        vLayout.addWidget(self.tabWidget)
        vLayout.addWidget(self.stackWidget)
        vLayout.addWidget(self.statusBar)
        layout.addLayout(vLayout)
        layout.addWidget(self.propertiesView)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        self.setWindowTitle(self.fontTitle())

        elements = [
            ("Ctrl+D", self.deselect),
            (platformSpecific.closeKeySequence(), self.closeGlyphTab),
            # XXX: does this really not warrant widget focus?
            (QKeySequence.Delete, self.delete),
            ("Shift+" + QKeySequence(
                QKeySequence.Delete).toString(), self.delete),
        ]
        for keys, callback in elements:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(callback)

        self.readSettings()
        self.statusBar.sizeChanged.connect(self.writeSettings)

    def readSettings(self):
        geometry = settings.fontWindowGeometry()
        if geometry:
            self.restoreGeometry(geometry)
        cellSize = settings.glyphCellSize()
        self.statusBar.setSize(cellSize)
        hidden = settings.propertiesHidden()
        if not hidden:
            self.properties()

    def writeSettings(self):
        settings.setFontWindowGeometry(self.saveGeometry())
        settings.setGlyphCellSize(self.glyphCellView.cellSize()[0])
        settings.setPropertiesHidden(self.propertiesView.isHidden())

    def menuBar(self):
        return self.layout().menuBar()

    def setMenuBar(self, menuBar):
        self.layout().setMenuBar(menuBar)

    def setupMenu(self, menuBar):
        app = QApplication.instance()

        fileMenu = menuBar.fetchMenu(Entries.File)
        fileMenu.fetchAction(Entries.File_New)
        fileMenu.fetchAction(Entries.File_Open)
        fileMenu.fetchMenu(Entries.File_Open_Recent)
        if not platformSpecific.mergeOpenAndImport():
            fileMenu.fetchAction(Entries.File_Import)
        fileMenu.addSeparator()
        fileMenu.fetchAction(Entries.File_Save, self.saveFile)
        fileMenu.fetchAction(Entries.File_Save_As, self.saveFileAs)
        fileMenu.fetchAction(Entries.File_Save_All)
        fileMenu.fetchAction(Entries.File_Reload, self.reloadFile)
        fileMenu.addSeparator()
        fileMenu.fetchAction(Entries.File_Export, self.exportFile)
        fileMenu.fetchAction(Entries.File_Exit)

        editMenu = menuBar.fetchMenu(Entries.Edit)
        self._undoAction = editMenu.fetchAction(Entries.Edit_Undo, self.undo)
        self._redoAction = editMenu.fetchAction(Entries.Edit_Redo, self.redo)
        editMenu.addSeparator()
        cut = editMenu.fetchAction(Entries.Edit_Cut, self.cut)
        copy = editMenu.fetchAction(Entries.Edit_Copy, self.copy)
        copyComponent = editMenu.fetchAction(
            Entries.Edit_Copy_As_Component, self.copyAsComponent)
        paste = editMenu.fetchAction(Entries.Edit_Paste, self.paste)
        self._clipboardActions = (cut, copy, copyComponent, paste)
        editMenu.fetchAction(Entries.Edit_Select_All, self.selectAll)
        # editMenu.fetchAction(Entries.Edit_Deselect, self.deselect)
        editMenu.fetchAction(Entries.Edit_Find, self.findGlyph)
        editMenu.addSeparator()
        editMenu.fetchAction(Entries.Edit_Settings)

        viewMenu = menuBar.fetchMenu(Entries.View)
        viewMenu.fetchAction(Entries.View_Zoom_In, lambda: self.zoom(1))
        viewMenu.fetchAction(Entries.View_Zoom_Out, lambda: self.zoom(-1))
        viewMenu.fetchAction(Entries.View_Reset_Zoom, self.resetZoom)
        viewMenu.addSeparator()
        viewMenu.fetchAction(
            Entries.View_Next_Tab, lambda: self.tabOffset(1))
        viewMenu.fetchAction(
            Entries.View_Previous_Tab, lambda: self.tabOffset(-1))
        viewMenu.fetchAction(
            Entries.View_Next_Glyph, lambda: self.glyphOffset(1))
        viewMenu.fetchAction(
            Entries.View_Previous_Glyph, lambda: self.glyphOffset(-1))
        viewMenu.fetchAction(
            Entries.View_Layer_Up, lambda: self.layerOffset(-1))
        viewMenu.fetchAction(
            Entries.View_Layer_Down, lambda: self.layerOffset(1))
        viewMenu.addSeparator()
        viewMenu.fetchAction(Entries.View_Show_Points)
        viewMenu.fetchAction(Entries.View_Show_Metrics)
        viewMenu.fetchAction(Entries.View_Show_Images)
        viewMenu.fetchAction(Entries.View_Show_Guidelines)

        fontMenu = menuBar.fetchMenu(Entries.Font)
        fontMenu.fetchAction(Entries.Font_Font_Info, self.fontInfo)
        fontMenu.fetchAction(Entries.Font_Font_Features, self.fontFeatures)
        fontMenu.addSeparator()
        fontMenu.fetchAction(Entries.Font_Add_Glyphs, self.addGlyphs)
        fontMenu.fetchAction(Entries.Font_Sort, self.sortGlyphs)

        # glyphMenu = menuBar.fetchMenu(self.tr("&Glyph"))
        # self._layerAction = glyphMenu.fetchAction(
        #     self.tr("&Layer Actions…"), self.layerActions, "L")

        menuBar.fetchMenu(Entries.Scripts)

        windowMenu = menuBar.fetchMenu(Entries.Window)
        windowMenu.fetchAction(Entries.Window_Groups, self.groups)
        windowMenu.fetchAction(Entries.Window_Metrics, self.metrics)
        windowMenu.fetchAction(Entries.Window_Scripting)
        windowMenu.fetchAction(Entries.Window_Properties, self.properties)
        windowMenu.addSeparator()
        action = windowMenu.fetchAction(Entries.Window_Output)
        action.setEnabled(app.outputWindow is not None)

        helpMenu = menuBar.fetchMenu(Entries.Help)
        helpMenu.fetchAction(Entries.Help_Documentation)
        helpMenu.fetchAction(Entries.Help_Report_An_Issue)
        helpMenu.addSeparator()
        helpMenu.fetchAction(Entries.Help_About)

        self._updateGlyphActions()

    # --------------
    # Custom methods
    # --------------

    def font_(self):
        return self._font

    def setFont_(self, font):
        if self._font is not None:
            self._font.removeObserver(self, "Font.Changed")
            self._font.removeObserver(self, "Font.GlyphOrderChanged")
            self._font.removeObserver(self, "Font.SortDescriptorChanged")
        self._font = font
        if font is None:
            return
        self._updateGlyphsFromGlyphOrder()
        font.addObserver(self, "_fontChanged", "Font.Changed")
        font.addObserver(
            self, "_glyphOrderChanged", "Font.GlyphOrderChanged")
        font.addObserver(
            self, "_sortDescriptorChanged", "Font.SortDescriptorChanged")

    def fontTitle(self):
        if self._font is None:
            return None
        path = self._font.path or self._font.binaryPath
        if path is not None:
            return os.path.basename(path.rstrip(os.sep))
        return self.tr("Untitled")

    def isGlyphTab(self):
        return bool(self.stackWidget.currentIndex())

    def openGlyphTab(self, glyph):
        # if a tab with this glyph exists already, switch to it
        for index in range(self.stackWidget.count()):
            if not index:
                continue
            view = self.stackWidget.widget(index)
            if view.widget().glyph() == glyph:
                self.tabWidget.setCurrentTab(index)
                return
        # spawn
        widget = GlyphCanvasView(self)
        # TODO: this should be in the widget
        widget.setFrameShape(widget.NoFrame)
        widget.setGlyph(glyph)
        widget.pointSizeModified.connect(self.statusBar.setSize)
        # add
        self.tabWidget.addTab(glyph.name)
        self.stackWidget.addWidget(widget)
        # activate
        self.tabWidget.setCurrentTab(-1)
        # XXX: drop the need for this, i.e. catch keys in the window
        widget.setFocus(True)

    def closeGlyphTab(self):
        index = self.stackWidget.currentIndex()
        if index:
            self.tabWidget.removeTab(index)

    def maybeSaveBeforeExit(self):
        if self._font.dirty:
            ret = CloseMessageBox.getCloseDocument(self, self.fontTitle())
            if ret == QMessageBox.Save:
                self.saveFile()
                return True
            elif ret == QMessageBox.Discard:
                return True
            return False
        return True

    # -------------
    # Notifications
    # -------------

    # app

    def _drawingToolRegistered(self, notification):
        tool = notification.data["tool"]
        self.toolBar.addTool(tool)

    def _drawingToolUnregistered(self, notification):
        tool = notification.data["tool"]
        self.toolBar.removeTool(tool)

    def _glyphViewGlyphChanged(self, notification):
        self._updateGlyphActions()
        index = self.stackWidget.currentIndex()
        if not index:
            return
        widget = self.stackWidget.currentWidget()
        glyph = widget.glyph()
        # XXX: subscribe to Glyph.NameChanged
        self.tabWidget.setTabName(index, glyph.name)

    # widgets

    def _sizeChanged(self):
        size = self.statusBar.size()
        if self.isGlyphTab():
            widget = self.stackWidget.currentWidget()
            widget.setPointSize(size)
        else:
            self.glyphCellView.setCellSize(size)

    def _tabChanged(self, index):
        self.statusBar.setShouldPropagateSize(not index)
        self.stackWidget.setCurrentIndex(index)
        parent = self.stackWidget.currentWidget().widget(
            ) if index else None
        for tool in QApplication.instance().drawingTools():
            tool.setParent(parent)

    def _widgetChanged(self, index):
        # update current glyph
        self._updateCurrentGlyph()
        # update undo/redo
        self._updateGlyphActions()
        # update slider
        if self.isGlyphTab():
            lo, hi = 600, 12000
            widget = self.stackWidget.currentWidget()
            size = widget.pointSize()
        else:
            lo, hi = 32, 128
            size = self.glyphCellView.cellSize()[0]
        self.statusBar.setMinimumSize(lo)
        self.statusBar.setMaximumSize(hi)
        self.statusBar.setSize(size)
        # XXX: coupling
        self.statusBar.selectionLabel.setVisible(not self.isGlyphTab())
        # update and connect setCurrentTool
        try:
            self.toolBar.currentToolChanged.disconnect()
        except TypeError:
            pass
        if not index:
            return
        widget = self.stackWidget.currentWidget()
        widget.setCurrentTool(self.toolBar.currentTool())
        self.toolBar.currentToolChanged.connect(widget.setCurrentTool)

    def _orderChanged(self):
        # TODO: reimplement when we start showing glyph subsets
        glyphs = self.glyphCellView.glyphs()
        self._font.glyphOrder = [glyph.name for glyph in glyphs]

    def _selectionChanged(self):
        # currentGlyph
        lastSelectedGlyph = self.glyphCellView.lastSelectedGlyph()
        app = QApplication.instance()
        app.setCurrentGlyph(lastSelectedGlyph)
        # selection text
        # TODO: this should probably be internal to the label
        selection = self.glyphCellView.selection()
        if selection is not None:
            count = len(selection)
            if count == 1:
                glyph = self.glyphCellView.glyphsForIndexes(selection)[0]
                text = "%s " % glyph.name
            else:
                text = ""
            if count:
                text = self.tr("{0}(%n selected)".format(text), n=count)
        else:
            text = ""
        self.statusBar.setText(text)
        # actions
        self._updateGlyphActions()

    # defcon

    def _fontChanged(self, notification):
        font = notification.object
        self.setWindowModified(font.dirty)

    def _glyphOrderChanged(self, notification):
        self._updateGlyphsFromGlyphOrder()

    def _updateGlyphsFromGlyphOrder(self):
        font = self._font
        glyphOrder = font.glyphOrder
        if glyphOrder:
            glyphCount = 0
            glyphs = []
            for glyphName in glyphOrder:
                if glyphName in font:
                    glyph = font[glyphName]
                    glyphCount += 1
                else:
                    glyph = font.get(glyphName, asTemplate=True)
                glyphs.append(glyph)
            if glyphCount < len(font):
                # if some glyphs in the font are not present in the glyph
                # order, loop again to add them at the end
                for glyph in font:
                    if glyph not in glyphs:
                        glyphs.append(glyph)
                font.disableNotifications(observer=self)
                font.glyphOrder = [glyph.name for glyph in glyphs]
                font.enableNotifications(observer=self)
        else:
            glyphs = list(font)
            font.disableNotifications(observer=self)
            font.glyphOrder = [glyph.name for glyph in glyphs]
            font.enableNotifications(observer=self)
        self.glyphCellView.setGlyphs(glyphs)

    def _sortDescriptorChanged(self, notification):
        font = notification.object
        descriptors = notification.data["newValue"]
        if descriptors[0]["type"] == "glyphSet":
            glyphNames = descriptors[0]["glyphs"]
        else:
            glyphNames = font.unicodeData.sortGlyphNames(
                font.keys(), descriptors)
        font.glyphOrder = glyphNames

    # ------------
    # Menu methods
    # ------------

    # File

    def saveFile(self, path=None, ufoFormatVersion=3):
        if path is None and self._font.path is None:
            self.saveFileAs()
        else:
            if path is None:
                path = self._font.path
            self._font.save(path, ufoFormatVersion)

    def saveFileAs(self):
        fileFormats = OrderedDict([
            (self.tr("UFO Font version 3 {}").format("(*.ufo)"), 3),
            (self.tr("UFO Font version 2 {}").format("(*.ufo)"), 2),
        ])
        state = settings.saveFileDialogState()
        path = self._font.path or self._font.binaryPath
        if path:
            directory = os.path.dirname(path)
        else:
            directory = None if state else QStandardPaths.standardLocations(
                QStandardPaths.DocumentsLocation)[0]
        # TODO: switch to directory dlg on platforms that need it
        dialog = QFileDialog(
            self, self.tr("Save File"), directory,
            ";;".join(fileFormats.keys()))
        if state:
            dialog.restoreState(state)
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        if directory:
            dialog.setDirectory(directory)
        ok = dialog.exec_()
        settings.setSaveFileDialogState(dialog.saveState())
        if ok:
            nameFilter = dialog.selectedNameFilter()
            path = dialog.selectedFiles()[0]
            self.saveFile(path, fileFormats[nameFilter])
            self.setWindowTitle(self.fontTitle())
        # return ok

    def reloadFile(self):
        font = self._font
        path = font.path or font.binaryPath
        if not font.dirty or path is None:
            return
        if not ReloadMessageBox.getReloadDocument(self, self.fontTitle()):
            return
        if font.path is not None:
            font.reloadInfo()
            font.reloadKerning()
            font.reloadGroups()
            font.reloadFeatures()
            font.reloadLib()
            font.reloadGlyphs(font.keys())
            font.dirty = False
        else:
            # TODO: we should do this in-place
            font_ = font.__class__().new()
            font_.extract(font.binaryPath)
            self.setFont_(font_)

    def exportFile(self):
        fileFormats = [
            (self.tr("PostScript OT font {}").format("(*.otf)")),
            (self.tr("TrueType OT font {}").format("(*.ttf)")),
        ]
        state = settings.exportFileDialogState()
        # TODO: font.path as default?
        # TODO: store per-font export path in lib
        directory = None if state else QStandardPaths.standardLocations(
            QStandardPaths.DocumentsLocation)[0]
        dialog = QFileDialog(
            self, self.tr("Export File"), directory,
            ";;".join(fileFormats))
        if state:
            dialog.restoreState(state)
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        ok = dialog.exec_()
        settings.setExportFileDialogState(dialog.saveState())
        if ok:
            fmt = "ttf" if dialog.selectedNameFilter(
                ) == fileFormats[1] else "otf"
            path = dialog.selectedFiles()[0]
            try:
                self._font.export(path, fmt)
            except Exception as e:
                errorReports.showCriticalException(e)

    # Edit

    def undo(self):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
        else:
            glyph = widget.lastSelectedGlyph()
        glyph.undo()

    def redo(self):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
        else:
            glyph = widget.lastSelectedGlyph()
        glyph.redo()

    def cut(self):
        self.copy()
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
            deleteUISelection(glyph)
        else:
            glyphs = widget.glyphs()
            for index in widget.selection():
                glyph = glyphs[index]
                glyph.prepareUndo()
                glyph.clear()

    def copy(self):
        widget = self.stackWidget.currentWidget()
        clipboard = QApplication.clipboard()
        mimeData = QMimeData()
        if self.isGlyphTab():
            glyph = widget.glyph()
            copyGlyph = glyph.getRepresentation("TruFont.FilterSelection")
            packGlyphs = (copyGlyph,)
        else:
            glyphs = self.glyphCellView.glyphs()
            packGlyphs = (
                glyphs[index] for index in sorted(
                    self.glyphCellView.selection()))
        pickled = []
        for glyph in packGlyphs:
            pickled.append(glyph.serialize(
                blacklist=("name", "unicode")
            ))
        mimeData.setData("application/x-trufont-glyph-data",
                         pickle.dumps(pickled))
        clipboard.setMimeData(mimeData)

    def copyAsComponent(self):
        widget = self.stackWidget.currentWidget()
        glyphs = widget.glyphs()
        pickled = []
        for index in widget.selection():
            glyph = glyphs[index]
            componentGlyph = glyph.__class__()
            componentGlyph.width = glyph.width
            component = componentGlyph.instantiateComponent()
            component.baseGlyph = glyph.name
            pickled.append(componentGlyph.serialize())
        clipboard = QApplication.clipboard()
        mimeData = QMimeData()
        mimeData.setData("application/x-trufont-glyph-data",
                         pickle.dumps(pickled))
        clipboard.setMimeData(mimeData)

    def paste(self):
        isGlyphTab = self.isGlyphTab()
        widget = self.stackWidget.currentWidget()
        if isGlyphTab:
            glyphs = (widget.glyph(),)
        else:
            selection = self.glyphCellView.selection()
            glyphs = widget.glyphsForIndexes(selection)
        clipboard = QApplication.clipboard()
        mimeData = clipboard.mimeData()
        if mimeData.hasFormat("application/x-trufont-glyph-data"):
            data = pickle.loads(mimeData.data(
                "application/x-trufont-glyph-data"))
            if len(data) == len(glyphs):
                for pickled, glyph in zip(data, glyphs):
                    if isGlyphTab:
                        pasteGlyph = glyph.__class__()
                        pasteGlyph.deserialize(pickled)
                        # TODO: if we serialize selected state, we don't need
                        # to do this
                        pasteGlyph.selected = True
                        if len(pasteGlyph) or len(pasteGlyph.components) or \
                                len(pasteGlyph.anchors):
                            glyph.prepareUndo()
                            pen = glyph.getPointPen()
                            # contours, components
                            pasteGlyph.drawPoints(pen)
                            # anchors
                            for anchor in pasteGlyph.anchors:
                                # XXX: bad API
                                anchor._glyph = None
                                anchor.selected = True
                                glyph.appendAnchor(anchor)
                    else:
                        # XXX: prune
                        glyph.prepareUndo()
                        glyph.deserialize(pickled)
        elif mimeData.hasText():
            if len(glyphs) == 1:
                glyph = glyphs[0]
                otherGlyph = glyph.__class__()
                text = mimeData.text()
                try:
                    readGlyphFromString(
                        text, otherGlyph, otherGlyph.getPointPen())
                except:
                    return
                glyph.prepareUndo()
                if not isGlyphTab:
                    glyph.clear()
                otherGlyph.drawPoints(glyph.getPointPen())

    def selectAll(self):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
            if glyph.selected:
                for anchor in glyph.anchors:
                    anchor.selected = True
                for component in glyph.components:
                    component.selected = True
            else:
                glyph.selected = True
        else:
            widget.selectAll()

    def deselect(self):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
            for anchor in glyph.anchors:
                anchor.selected = False
            for component in glyph.components:
                component.selected = False
            glyph.selected = False
        else:
            widget.setSelection(set())

    def delete(self):
        modifiers = QApplication.keyboardModifiers()
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
            # TODO: fuse more the two methods, they're similar and delete is
            # Cut except not putting in the clipboard
            if modifiers & Qt.AltModifier:
                deleteUISelection(glyph)
            else:
                preserveShape = not modifiers & Qt.ShiftModifier
                # TODO: prune
                glyph.prepareUndo()
                removeUIGlyphElements(glyph, preserveShape)
        else:
            erase = modifiers & Qt.ShiftModifier
            if self._proceedWithDeletion(erase):
                glyphs = widget.glyphsForIndexes(widget.selection())
                for glyph in glyphs:
                    font = glyph.font
                    if erase:
                        del font[glyph.name]
                    else:
                        # TODO: consider doing that in glyph template setter
                        glyph.clear()
                        glyph.template = True

    def findGlyph(self):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
            newGlyph, ok = FindDialog.getNewGlyph(self, glyph)
            if ok and newGlyph is not None:
                widget.setGlyph(newGlyph)
        else:
            pass  # XXX

    # View

    def zoom(self, step):
        if self.isGlyphTab():
            widget = self.stackWidget.currentWidget()
            widget.zoom(step)
        else:
            value = self.statusBar.size()
            newValue = value + 10 * step
            self.statusBar.setSize(newValue)

    def resetZoom(self):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            widget.fitScaleBBox()
        else:
            settings.removeGlyphCellSize()
            cellSize = settings.glyphCellSize()
            self.statusBar.setSize(cellSize)

    def tabOffset(self, value):
        tab = self.tabWidget.currentTab()
        newTab = (tab + value) % len(self.tabWidget.tabs())
        self.tabWidget.setCurrentTab(newTab)

    def glyphOffset(self, value):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            currentGlyph = widget.glyph()
            font = currentGlyph.font
            glyphOrder = font.glyphOrder
            # should be enforced in fontView already
            if not (glyphOrder and len(glyphOrder)):
                return
            index = glyphOrder.index(currentGlyph.name)
            newIndex = (index + value) % len(glyphOrder)
            glyph = font[glyphOrder[newIndex]]
            widget.setGlyph(glyph)
        else:
            lastSelectedCell = widget.lastSelectedCell()
            if lastSelectedCell is None:
                return
            newIndex = lastSelectedCell + value
            if newIndex < 0 or newIndex >= len(widget.glyphs()):
                return
            widget.setSelection({newIndex})

    def layerOffset(self, value):
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            currentGlyph = widget.glyph()
            layerSet, layer = currentGlyph.layerSet, currentGlyph.layer
            if None in (layerSet, layer):
                return
            index = layerSet.layerOrder.index(layer.name)
            newIndex = (index + value) % len(layerSet)
            layer_ = layerSet[layerSet.layerOrder[newIndex]]
            if layer_ == layer:
                return
            # XXX: fix get
            # glyph = layer_.get(currentGlyph.name)
            if currentGlyph.name in layer_:
                glyph = layer_[currentGlyph.name]
            else:
                glyph = layer_.newGlyph(currentGlyph.name)
            widget.setGlyph(glyph)

    # Font

    def fontInfo(self):
        # If a window is already opened, bring it to the front, else spawn one.
        # TODO: see about using widget.setAttribute(Qt.WA_DeleteOnClose)
        # otherwise it seems we're just leaking memory after each close...
        # (both raise_ and show allocate memory instead of using the hidden
        # widget it seems)
        if self._infoWindow is not None and self._infoWindow.isVisible():
            self._infoWindow.raise_()
        else:
            self._infoWindow = FontInfoWindow(self._font, self)
            self._infoWindow.show()

    def fontFeatures(self):
        # TODO: see up here
        if self._featuresWindow is not None and self._featuresWindow.isVisible(
                ):
            self._featuresWindow.raise_()
        else:
            self._featuresWindow = FontFeaturesWindow(self._font, self)
            self._featuresWindow.show()

    def addGlyphs(self):
        glyphs = self.glyphCellView.glyphs()
        newGlyphNames, params, ok = AddGlyphsDialog.getNewGlyphNames(
            self, glyphs)
        if ok:
            sortFont = params.pop("sortFont")
            for name in newGlyphNames:
                glyph = self._font.get(name, **params)
                if glyph is not None:
                    glyphs.append(glyph)
            self.glyphCellView.setGlyphs(glyphs)
            if sortFont:
                # TODO: when the user add chars from a glyphSet and no others,
                # should we try to sort according to that glyphSet?
                # The above would probably warrant some rearchitecturing.
                # kick-in the sort mechanism
                self._font.sortDescriptor = self._font.sortDescriptor

    def sortGlyphs(self):
        sortDescriptor, ok = SortDialog.getDescriptor(
            self, self._font.sortDescriptor)
        if ok:
            self._font.sortDescriptor = sortDescriptor

    # Window

    def groups(self):
        # TODO: see up here
        if self._groupsWindow is not None and self._groupsWindow.isVisible():
            self._groupsWindow.raise_()
        else:
            self._groupsWindow = GroupsWindow(self._font, self)
            self._groupsWindow.show()

    def metrics(self):
        # TODO: see up here
        if self._metricsWindow is not None and self._metricsWindow.isVisible():
            self._metricsWindow.raise_()
        else:
            self._metricsWindow = MetricsWindow(self._font)
            # XXX: need proper, fast windowForFont API!
            self._metricsWindow._fontWindow = self
            self.destroyed.connect(self._metricsWindow.close)
            self._metricsWindow.show()
        # TODO: default string kicks-in on the window before this. Figure out
        # how to make a clean interface
        selection = self.glyphCellView.selection()
        if selection:
            glyphs = self.glyphCellView.glyphsForIndexes(selection)
            self._metricsWindow.setGlyphs(glyphs)

    def properties(self):
        shouldBeVisible = self.propertiesView.isHidden()
        self.propertiesView.setVisible(shouldBeVisible)
        self.writeSettings()

    # update methods

    def _updateCurrentGlyph(self):
        # TODO: refactor this pattern...
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            glyph = widget.glyph()
        else:
            glyph = widget.lastSelectedGlyph()
        if glyph is not None:
            app = QApplication.instance()
            app.setCurrentGlyph(glyph)

    def _updateGlyphActions(self):
        if not hasattr(self, "_undoAction"):
            return
        widget = self.stackWidget.currentWidget()
        if self.isGlyphTab():
            currentGlyph = widget.glyph()
        else:
            currentGlyph = widget.lastSelectedGlyph()
        # disconnect eventual signal of previous glyph
        objects = (
            (self._undoAction, self.undo),
            (self._redoAction, self.redo),
        )
        for action, slot in objects:
            try:
                action.disconnect()
            except TypeError:
                pass
            action.triggered.connect(slot)
        # now update status
        if currentGlyph is None:
            self._undoAction.setEnabled(False)
            self._redoAction.setEnabled(False)
        else:
            undoManager = currentGlyph.undoManager
            self._undoAction.setEnabled(currentGlyph.canUndo())
            undoManager.canUndoChanged.connect(self._undoAction.setEnabled)
            self._redoAction.setEnabled(currentGlyph.canRedo())
            undoManager.canRedoChanged.connect(self._redoAction.setEnabled)
        # and other actions
        for action in self._clipboardActions:
            action.setEnabled(currentGlyph is not None)

    # helper

    def _proceedWithDeletion(self, erase=False):
            if not self.glyphCellView.selection():
                return
            tr = self.tr("Delete") if erase else self.tr("Clear")
            text = self.tr("Do you want to %s selected glyphs?") % tr.lower()
            closeDialog = QMessageBox(
                QMessageBox.Question, "",
                self.tr("%s glyphs") % tr,
                QMessageBox.Yes | QMessageBox.No, self)
            closeDialog.setInformativeText(text)
            closeDialog.setModal(True)
            ret = closeDialog.exec_()
            if ret == QMessageBox.Yes:
                return True
            return False

    # ----------
    # Qt methods
    # ----------

    def setWindowTitle(self, title):
        if platformSpecific.appNameInTitle():
            title += " – TruFont"
        super().setWindowTitle("[*]{}".format(title))

    def sizeHint(self):
        return QSize(1270, 800)

    def moveEvent(self, event):
        self.writeSettings()

    resizeEvent = moveEvent

    def showEvent(self, event):
        app = QApplication.instance()
        data = dict(
            font=self._font,
            window=self,
        )
        app.postNotification("fontWindowWillOpen", data)
        super().showEvent(event)
        app.postNotification("fontWindowOpened", data)

    def closeEvent(self, event):
        ok = self.maybeSaveBeforeExit()
        if ok:
            app = QApplication.instance()
            data = dict(
                font=self._font,
                window=self,
            )
            app.postNotification("fontWindowWillClose", data)
            self._font.removeObserver(self, "Font.Changed")
            app = QApplication.instance()
            app.dispatcher.removeObserver(self, "drawingToolRegistered")
            app.dispatcher.removeObserver(self, "drawingToolUnregistered")
            app.dispatcher.removeObserver(self, "glyphViewGlyphChanged")
            event.accept()
        else:
            event.ignore()

    def event(self, event):
        if event.type() == QEvent.WindowActivate:
            app = QApplication.instance()
            app.setCurrentMainWindow(self)
            self._updateCurrentGlyph()
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(212, 212, 212))
