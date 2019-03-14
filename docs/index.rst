The After Effects Engine
===========================================


The engine uses ``tk-framework-adobe`` to wrap the Javascript API to python.
To get or set a variable or to call a method in javascript one may do so by using
the engine's "adobe" member.

An example:

    Assuming Aftereffects has a comp-item selected.
    To get the layerName of the currently active comp item one can use the following python commands::

        comp_item = engine.adobe.app.project.active_item
        layer1 = comp_item.active_item.layers[1]
        print layer1.name

    The Javascript equivalent to this would be::

        var comp_item = app.project.active_item
        var layer1 = comp_item.layers[1]
        $.writeln(layer1.name)


.. note::

    Everytime a datatype of an argument is describes as adobe.[...]Object it refers to the
    Adobe JavaScript equivalent.

    To see the reference of the After Effects Javascript API please visit `this link`_ or download `this pdf`_

    .. _this link: http://docs.aenhancers.com/introduction/overview/
    .. _this pdf: http://blogs.adobe.com/wp-content/blogs.dir/48/files/2012/06/After-Effects-CS6-Scripting-Guide.pdf?file=2012/06/After-Effects-CS6-Scripting-Guide.pdf

Contents:

.. toctree::
   :maxdepth: 2

   contributing
   api
   hooks
